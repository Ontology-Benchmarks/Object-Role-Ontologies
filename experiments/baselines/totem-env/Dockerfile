# TOTeM's pinned environment (Pipfile: python_full_version 3.9.13, pm4py 2.2.32), installed
# from the repository's own Pipfile.lock.
FROM python:3.9.13
RUN pip install --no-cache-dir pipenv==2023.12.1
WORKDIR /env
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy
# Imported by the vendored ocpa package but missing from Pipfile.lock; versions current as of
# the lockfile (March 2024).
RUN pip install --no-cache-dir jsonschema==4.21.1 seaborn==0.13.2
WORKDIR /work
