"""Connect to the Konfuzio Server to receive or send data."""

import json
import logging
import os
from operator import itemgetter
from typing import List, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from konfuzio_sdk import KONFUZIO_HOST, KONFUZIO_TOKEN
from konfuzio_sdk.urls import (
    get_auth_token_url,
    get_projects_list_url,
    get_document_api_details_url,
    get_project_url,
    get_document_ocr_file_url,
    get_document_original_file_url,
    get_documents_meta_url,
    get_document_annotations_url,
    get_annotation_url,
    get_upload_document_url,
    get_document_url,
    get_document_segmentation_details_url,
    get_labels_url,
    get_update_ai_model_url,
    get_create_ai_model_url,
)
from konfuzio_sdk.utils import is_file

logger = logging.getLogger(__name__)


def _get_auth_token(username, password, host=KONFUZIO_HOST) -> str:
    """
    Generate the authentication token for the user.

    :return: The new generated token.
    """
    url = get_auth_token_url(host)
    user_credentials = {"username": username, "password": password}
    r = requests.post(url, json=user_credentials)
    if r.status_code == 200:
        token = json.loads(r.text)['token']
    else:
        raise ValueError(
            "[ERROR] Your credentials are not correct! Please run init again and provide the correct credentials."
        )
    return token


class TimeoutHTTPAdapter(HTTPAdapter):
    """Combine a retry strategy with a timeout strategy.

    Documentation
    =============
        * `Urllib3
            <https://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html#urllib3.util.Retry>`__
        * `Blogpost with TimeoutHTTPAdapter idea
            <https://findwork.dev/blog/advanced-usage-python-requests-timeouts-retries-hooks/>`__
    """

    def __init__(self, timeout, *args, **kwargs):
        """Force to init with timout policy."""
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        """Use timeout policy if not otherwise declared."""
        timeout = kwargs.get("timeout")
        if timeout is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, *args, **kwargs)


def _konfuzio_session(token=KONFUZIO_TOKEN):
    """
    Create a session incl. base auth to the KONFUZIO_HOST.

    :return: Request session.
    """
    retry_strategy = Retry(
        total=5,
        status_forcelist=[429, 500, 502, 503, 504],
        backoff_factor=2,
        method_whitelist=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"],  # POST excluded
    )
    session = requests.Session()
    session.mount('https://', adapter=TimeoutHTTPAdapter(max_retries=retry_strategy, timeout=120))
    session.headers.update({'Authorization': f'Token {token}'})
    return session


def get_project_list(session=_konfuzio_session()):
    """
    Get the list of all projects for the user.

    :param session: Konfuzio session with Retry and Timeout policy
    :return: Response object
    """
    url = get_projects_list_url()
    r = session.get(url=url)
    return r.json()


def get_project_details(project_id: int, session=_konfuzio_session()) -> dict:
    """
    Get Label Sets available in project.

    :param project_id: ID of the project
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Sorted Label Sets.
    """
    url = get_project_url(project_id=project_id)
    r = session.get(url=url)
    r.raise_for_status()
    return r.json()


def create_new_project(project_name, session=_konfuzio_session()):
    """
    Create a new project for the user.

    :param project_name: name of the project you want to create
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Response object
    """
    url = get_projects_list_url()
    new_project_data = {"name": project_name}
    r = session.post(url=url, json=new_project_data)

    if r.status_code == 201:
        project_id = json.loads(r.text)["id"]
        print(f"Project {project_name} (ID {project_id}) was created successfully!")
        return project_id
    else:
        raise Exception(f'The project {project_name} was not created, please check your permissions.')


def get_document_details(
    document_id: int, project_id: int, session=_konfuzio_session(), extra_fields: str = 'bbox,hocr'
):
    """
    Use the text-extraction server to retrieve the data from a document.

    :param document_id: ID of the document
    :param project_id: ID of the project
    :param session: Konfuzio session with Retry and Timeout policy
    :param extra_fields: Retrieve bounding boxes and HOCR from document, too.
    :return: Data of the document.
    """
    url = get_document_api_details_url(document_id=document_id, project_id=project_id, extra_fields=extra_fields)
    r = session.get(url)
    data = json.loads(r.text)

    return data


def post_document_bulk_annotation(document_id: int, project_id: int, annotation_list, session=_konfuzio_session()):
    """
    Add a list of annotations to an existing document.

    :param document_id: ID of the file
    :param project_id: ID of the project
    :param annotation_list: List of annotations
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Response status.
    """
    url = get_document_annotations_url(document_id, project_id=project_id)
    r = session.post(url, json=annotation_list)
    r.raise_for_status()
    return r


def post_document_annotation(
    document_id: int,
    project_id: int,
    label_id: int,
    label_set_id: int,
    confidence: Union[float, None] = None,
    revised: bool = False,
    is_correct: bool = False,
    annotation_set=None,
    session=_konfuzio_session(),
    **kwargs,
):
    """
    Add an annotation to an existing document.

    For the annotation set definition, we can:
    - define the annotation set id_ where the annotation should belong
    (annotation_set=x (int), define_annotation_set=True)
    - pass it as None and a new annotation set will be created
    (annotation_set=None, define_annotation_set=True)
    - do not pass the annotation set field and a new annotation set will be created if does not exist any or the
    annotation will be added to the previous annotation set created (define_annotation_set=False)

    :param document_id: ID of the file
    :param project_id: ID of the project
    :param label_id: ID of the label.
    :param label_set_id: ID of the label set where the annotation belongs
    :param confidence: Confidence of the Annotation still called Accuracy by text-annotation
    :param revised: If the annotation is revised or not (bool)
    :param is_correct: If the annotation is corrected or not (bool)
    :param annotation_set: Annotation set to connect to the server
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Response status.
    """
    url = get_document_annotations_url(document_id, project_id=project_id)

    bbox = kwargs.get('bbox', None)
    custom_bboxes = kwargs.get('bboxes', None)
    selection_bbox = kwargs.get('selection_bbox', None)
    page_number = kwargs.get('page_number', None)
    offset_string = kwargs.get('offset_string', None)
    start_offset = kwargs.get('start_offset', None)
    end_offset = kwargs.get('end_offset', None)

    data = {
        'start_offset': start_offset,
        'end_offset': end_offset,
        'label': label_id,
        'revised': revised,
        'section_label_id': label_set_id,
        'accuracy': confidence,
        'is_correct': is_correct,
    }

    if end_offset:
        data['end_offset'] = end_offset

    if start_offset is not None:
        data['start_offset'] = start_offset

    if annotation_set:
        data['section'] = annotation_set
    else:
        data['section'] = None

    if page_number is not None:
        data['page_number'] = page_number

    if offset_string is not None:
        data['offset_string'] = offset_string

    if bbox is not None:
        data['bbox'] = bbox

    if custom_bboxes is not None:
        data['custom_bboxes'] = custom_bboxes

    if selection_bbox is not None:
        data['selection_bbox'] = selection_bbox

    r = session.post(url, json=data)
    assert r.status_code == 201
    return r


def delete_document_annotation(document_id: int, annotation_id: int, project_id: int, session=_konfuzio_session()):
    """
    Delete a given annotation of the given document.

    :param document_id: ID of the document
    :param annotation_id: ID of the annotation
    :param project_id: ID of the project
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Response status.
    """
    url = get_annotation_url(document_id=document_id, annotation_id=annotation_id, project_id=project_id)
    r = session.delete(url)
    if r.status_code == 200:
        # the text annotation received negative feedback and copied the annotation and created a new one
        return json.loads(r.text)['id']
    elif r.status_code == 204:
        return r


def get_meta_of_files(project_id: int, session=_konfuzio_session()) -> List[dict]:
    """
    Get dictionary of previously uploaded document names to Konfuzio API.

    Dataset_status:
    NONE = 0
    PREPARATION = 1
    TRAINING = 2
    TEST = 3
    LOW_OCR_QUALITY = 4

    :param project_id: ID of the project
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Sorted documents names in the format {id_: 'pdf_name'}.
    """
    url = get_documents_meta_url(project_id=project_id)
    result = []

    while True:
        r = session.get(url)
        data = r.json()
        if isinstance(data, dict) and 'results' in data.keys():
            result += data['results']
            if 'next' in data.keys() and data['next']:
                url = data['next']
            else:
                break
        else:
            result = data
            break

    sorted_documents = sorted(result, key=itemgetter('id'))
    return sorted_documents


def create_label(
    project_id: int, label_name: str, label_sets: list, session=_konfuzio_session(), **kwargs
) -> List[dict]:
    """
    Create a Label and associate it with labels sets.

    :param project_id: Project ID where to create the label
    :param label_name: Name for the label
    :param label_sets: Label sets that use the label
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Label ID in the Konfuzio Server.
    """
    url = get_labels_url()
    label_sets_ids = [label_set.id_ for label_set in label_sets]

    description = kwargs.get('description', None)
    has_multiple_top_candidates = kwargs.get('has_multiple_top_candidates', False)
    data_type = kwargs.get('data_type', 'Text')

    data = {
        "project": project_id,
        "text": label_name,
        "description": description,
        "has_multiple_top_candidates": has_multiple_top_candidates,
        "get_data_type_display": data_type,
        "templates": label_sets_ids,
    }

    r = session.post(url=url, json=data)

    assert r.status_code == requests.codes.created, f'Status of request: {r}'
    label_id = r.json()['id_']
    return label_id


def upload_file_konfuzio_api(
    filepath: str,
    project_id: int,
    dataset_status: int = 0,
    session=_konfuzio_session(),
    category_id: Union[None, int] = None,
):
    """
    Upload Document to Konfuzio API.

    :param filepath: Path to file to be uploaded
    :param project_id: ID of the project
    :param session: Konfuzio session with Retry and Timeout policy
    :param dataset_status: Set data set status of the document.
    :param category_id: Define a category the document belongs to
    :return: Response status.
    """
    url = get_upload_document_url()
    is_file(filepath)

    with open(filepath, "rb") as f:
        file_data = f.read()

    files = {"data_file": (os.path.basename(filepath), file_data, "multipart/form-data")}
    data = {"project": project_id, "dataset_status": dataset_status, "category_template": category_id}

    r = session.post(url=url, files=files, data=data)
    return r


def delete_file_konfuzio_api(document_id: int, session=_konfuzio_session()):
    """
    Delete Document by ID via Konfuzio API.

    :param document_id: ID of the document
    :param session: Konfuzio session with Retry and Timeout policy
    :return: File id_ in Konfuzio Server.
    """
    url = get_document_url(document_id)
    data = {'id': document_id}

    r = session.delete(url=url, json=data)
    assert r.status_code == 204
    return True


def update_file_konfuzio_api(
    document_id: int, file_name: str, dataset_status: int, session=_konfuzio_session(), **kwargs
):
    """
    Update the dataset status of an existing document via Konfuzio API.

    :param document_id: ID of the document
    :param file_name: New file name.
    :param dataset_status: Change or keep dataset status. Get document information first to keep the status.
    :param session: Konfuzio session with Retry and Timeout policy
    :return: Response status.
    """
    url = get_document_url(document_id)

    category_id = kwargs.get('category_template_id', None)

    data = {"data_file_name": file_name, "dataset_status": dataset_status, "category_template": category_id}

    r = session.patch(url=url, json=data)
    return json.loads(r.text)


def download_file_konfuzio_api(document_id: int, ocr: bool = True, session=_konfuzio_session()):
    """
    Download file from the Konfuzio server using the document id_.

    Django authentication is form-based, whereas DRF uses BasicAuth.

    :param document_id: ID of the document
    :param ocr: Bool to get the ocr version of the document
    :param session: Konfuzio session with Retry and Timeout policy
    :return: The downloaded file.
    """
    if ocr:
        url = get_document_ocr_file_url(document_id)
    else:
        url = get_document_original_file_url(document_id)

    r = session.get(url)

    content_type = r.headers.get('content-type')
    if content_type not in ['application/pdf', 'image/jpeg', 'image/png', 'image/jpg']:
        raise FileNotFoundError(f'CONTENT TYP of {document_id} is {content_type} and no PDF or image.')

    logger.info(f'Downloaded file {document_id} from {KONFUZIO_HOST}.')
    return r.content


def get_results_from_segmentation(doc_id: int, project_id: int, session=_konfuzio_session()) -> List[List[dict]]:
    """Get bbox results from segmentation endpoint.

    :param doc_id: ID of the document
    :param project_id: ID of the project.
    :param session: Konfuzio session with Retry and Timeout policy
    """
    segmentation_url = get_document_segmentation_details_url(doc_id, project_id)
    response = session.get(segmentation_url)
    segmentation_result = response.json()

    return segmentation_result


def upload_ai_model(ai_model_path: str, category_ids: List[int] = None, session=_konfuzio_session()):  # noqa: F821
    """
    Upload an ai_model to the text-annotation server.

    :param ai_model_path: Path to the ai_model
    :param category_ids: define ids of categories the model should become available after upload.
    :param session: session to connect to server
    :return:
    """
    url = get_create_ai_model_url()
    if is_file(ai_model_path):
        model_name = os.path.basename(ai_model_path)
        with open(ai_model_path, 'rb') as f:
            multipart_form_data = {'ai_model': (model_name, f)}
            headers = {"Prefer": "respond-async"}
            r = session.post(url, files=multipart_form_data, headers=headers)
            r.raise_for_status()
    data = r.json()
    ai_model_id = data['id']
    ai_model = data['ai_model']

    if category_ids:
        url = get_update_ai_model_url(ai_model_id)
        data = {'templates': category_ids}
        headers = {'content-type': 'application/json'}
        response = session.patch(url, data=json.dumps(data), headers=headers)
        response.raise_for_status()

    logger.info(f'New ai_model uploaded {ai_model} to {url}')
    return ai_model
