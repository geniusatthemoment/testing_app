"""Интеграционные тесты endpoint `POST /api/uploads` на живом backend."""

from __future__ import annotations

import uuid


def _multipart(file_field_name: str, filename: str | None, content_type: str, data: bytes):
    boundary = f"----codex{uuid.uuid4().hex}"
    parts = [f"--{boundary}\r\n".encode("utf-8")]

    disposition = f'Content-Disposition: form-data; name="{file_field_name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    parts.append((disposition + "\r\n").encode("utf-8"))
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return body, headers


class TestUploadEndpoint:
    def test_upload_photo_success(self, api_request):
        file_bytes = b"\x89PNG\r\n\x1a\nhello"
        body, headers = _multipart("file", "photo.png", "image/png", file_bytes)

        response = api_request("POST", "/api/uploads", raw_body=body, headers=headers)
        payload = response.body

        assert (
            response.status,
            str(payload.get("url", "")).startswith("/uploads/"),
            str(payload.get("filename", "")).endswith(".png"),
            payload.get("size"),
        ) == (201, True, True, len(file_bytes))

    def test_upload_photo_rejects_non_image(self, api_request):
        body, headers = _multipart("file", "notes.txt", "text/plain", b"not an image")

        response = api_request("POST", "/api/uploads", raw_body=body, headers=headers)

        assert (response.status, "только изображения" in str(response.body.get("error", "")).lower()) == (400, True)

    def test_upload_photo_rejects_missing_file_field(self, api_request):
        body, headers = _multipart("image", "photo.png", "image/png", b"fake")

        response = api_request("POST", "/api/uploads", raw_body=body, headers=headers)

        assert (response.status, "полем 'file'" in str(response.body.get("error", ""))) == (400, True)

    def test_upload_photo_rejects_empty_filename(self, api_request):
        body, headers = _multipart("file", None, "image/png", b"fake")

        response = api_request("POST", "/api/uploads", raw_body=body, headers=headers)

        assert (response.status, "имя файла" in str(response.body.get("error", "")).lower()) == (400, True)

    def test_upload_photo_rejects_when_multipart_missing(self, api_request):
        response = api_request("POST", "/api/uploads", json_payload={"file": "x"})

        assert (response.status, "multipart/form-data" in str(response.body.get("error", "")).lower()) == (400, True)
