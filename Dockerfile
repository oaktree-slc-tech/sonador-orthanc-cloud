# syntax = docker/dockerfile:experimental
FROM code.oak-tree.tech:5005/oak-tree/medical-imaging/imaging-development-env/orthanc-s3:latest
ARG CI_COMMIT_SHA

# Install Python Requests Module
RUN apt-get install -y python3-requests

# Install Sonador Python Plugin
RUN mkdir -p /opt/orthanc/
RUN --mount=type=secret,id=auto-devops-build-secrets . /run/secrets/auto-devops-build-secrets \
  && export CI_COMMIT_SHA=${CI_COMMIT_SHA:-master} \
  && echo "Build container for Sonador/Orthanc Plugin $CI_COMMIT_SHA" \
  && cd /opt/orthanc/ \
  && git clone https://code.oak-tree.tech/oak-tree/medical-imaging/orthanc-sonador.git \
  && cd orthanc-sonador && git checkout $CI_COMMIT_SHA \
  && git submodule update --init --recursive --remote \
  && pip3 install --timeout 300 -r requirements.txt
