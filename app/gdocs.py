"""
Fetch a Google Doc as DOCX from a public share URL.
The document must be shared as "Anyone with the link can view".
"""

import re
import tempfile
import requests
from pathlib import Path


EXPORT_URL = "https://docs.google.com/document/d/{doc_id}/export?format=docx"


def extract_doc_id(url: str) -> str:
    """Extract the Google Docs document ID from a share URL."""
    # Handles:
    #   https://docs.google.com/document/d/{id}/edit
    #   https://docs.google.com/document/d/{id}/view
    #   https://docs.google.com/document/d/{id}
    #   https://drive.google.com/file/d/{id}/view  (Drive links)
    match = re.search(r"/d/([a-zA-Z0-9_-]{25,})", url)
    if match:
        return match.group(1)
    # Plain doc ID passed directly
    if re.fullmatch(r"[a-zA-Z0-9_-]{25,}", url.strip()):
        return url.strip()
    raise ValueError(
        "Could not extract a Google Docs document ID from the URL. "
        "Make sure you're pasting a full Google Docs link."
    )


def fetch_docx(url: str, timeout: int = 30) -> Path:
    """
    Download the Google Doc as a .docx file into a temp location.
    Returns the Path to the downloaded file.
    Raises ValueError for access errors or invalid URLs.
    """
    doc_id = extract_doc_id(url)
    export_url = EXPORT_URL.format(doc_id=doc_id)

    try:
        response = requests.get(export_url, stream=True, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        raise ValueError(f"Network error fetching the document: {exc}") from exc

    if response.status_code == 401 or response.status_code == 403:
        raise ValueError(
            "Could not access the document. Make sure the Google Doc is shared "
            "as 'Anyone with the link can view'."
        )
    if response.status_code == 404:
        raise ValueError("Document not found. Check that the URL is correct.")
    if response.status_code != 200:
        raise ValueError(
            f"Unexpected response from Google ({response.status_code}). "
            "The document may be private or the URL is incorrect."
        )

    # Check content type — Google redirects to sign-in page for private docs
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type and "application/vnd" not in content_type:
        raise ValueError(
            "Google returned an HTML page instead of a document. "
            "Make sure the Google Doc is shared as 'Anyone with the link can view'."
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    for chunk in response.iter_content(chunk_size=8192):
        tmp.write(chunk)
    tmp.close()
    return Path(tmp.name)
