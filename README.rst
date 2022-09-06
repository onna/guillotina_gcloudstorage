GUILLOTINA_GCLOUDSTORAGE
========================

GCloud blob storage for guillotina.


Example config.json entry:

.. code-block:: json

    ...
    "cloud_storage": "guillotina_gcloudstorage.interfaces.IGCloudFileField",
    "cloud_datamanager": "redis",
    "load_utilities": {
        "gcloud": {
            "provides": "guillotina_gcloudstorage.interfaces.IGCloudBlobStore",
            "factory": "guillotina_gcloudstorage.storage.GCloudBlobStore",
            "settings": {
                "uniform_bucket_level_access": True,
                "json_credentials": "/path/to/credentials.json",
                "bucket": "name-of-bucket",
                "bucket_name_format": "{container}-foobar{delimiter}{base}",
                "bucket_labels": {
                    "foo": "bar"
                }
            }
        }
    }
    ...


Getting started with development
--------------------------------

Using pip (requires Python > 3.7):

.. code-block:: shell

    python3.7 -m venv .
    ./bin/pip install -e .[test]
    pre-commit install


Unit Tests & Testing
--------------------------------

The unit tests in this repo are a bit flaky and difficult to run. There is some level of reliance on having environment variables setup that point to an actual GCP environment, which you can find in `tests/fixtures.py`. Currently we rely on manually testing changes in order to verify functionality.