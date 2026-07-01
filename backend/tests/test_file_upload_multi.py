import io
import unittest
from unittest.mock import patch

import pandas as pd

import django
from django.conf import settings
if not settings.configured:
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory
from api import views


def _csv(df):
    b = io.BytesIO()
    df.to_csv(b, index=False)
    return b.getvalue()


class FileUploadMultiTests(unittest.TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("api.views.process_dataset_pipeline")
    @patch("api.views.insert_tracking_job", return_value={"id": "job-1", "current_step": 1})
    @patch("api.views.insert_dataset", return_value={"id": "ds-1"})
    @patch("api.views.upload_file_to_bucket", return_value="u/combined.csv")
    @patch("api.views.list_user_datasets", return_value=[])
    @patch("api.views._authenticate_request", return_value=("u", None))
    def test_two_matching_files_create_one_dataset(
        self, _auth, _list, mock_up, mock_ins, _job, _pipe
    ):
        a = SimpleUploadedFile("a.csv", _csv(pd.DataFrame({"id": [1], "v": [10]})), content_type="text/csv")
        b = SimpleUploadedFile("b.csv", _csv(pd.DataFrame({"id": [2], "v": [20]})), content_type="text/csv")
        req = self.factory.post("/api/file-upload/", {"file": [a, b], "category": "Sales"})
        resp = views.file_upload(req)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(mock_ins.call_count, 1)
        self.assertEqual(resp.data.get("accepted_files"), ["a.csv", "b.csv"])

    @patch("api.views.process_dataset_pipeline")
    @patch("api.views.insert_tracking_job", return_value={"id": "job-1", "current_step": 1})
    @patch("api.views.insert_dataset", return_value={"id": "ds-1"})
    @patch("api.views.upload_file_to_bucket", return_value="u/combined.csv")
    @patch("api.views.list_user_datasets", return_value=[])
    @patch("api.views._authenticate_request", return_value=("u", None))
    def test_mismatched_file_rejected_individually(
        self, _auth, _list, mock_up, mock_ins, _job, _pipe
    ):
        a = SimpleUploadedFile("a.csv", _csv(pd.DataFrame({"id": [1], "v": [10]})), content_type="text/csv")
        bad = SimpleUploadedFile("bad.csv", _csv(pd.DataFrame({"id": [2], "z": [9]})), content_type="text/csv")
        req = self.factory.post("/api/file-upload/", {"file": [a, bad], "category": "Sales"})
        resp = views.file_upload(req)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["accepted_files"], ["a.csv"])
        self.assertEqual(len(resp.data["rejected_files"]), 1)
