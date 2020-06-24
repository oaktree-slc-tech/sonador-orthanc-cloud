# syntax = docker/dockerfile:experimental
FROM code.oak-tree.tech:5005/oak-tree/medical-imaging/imaging-development-env/orthanc-s3:latest

# Install Python Requests Module
RUN pip3 install six requests tabulate==0.8.7

# Install Sonador Python Plugin
RUN --mount=type=secret,id=auto-devops-build-secrets . /run/secrets/auto-devops-build-secrets \
  && export CI_COMMIT_SHA=${CI_COMMIT_SHA:-master} \
  && echo "Build container for Sonador/Orthanc Plugin $CI_COMMIT_SHA" \
  && mkdir -p /opt/orthanc/ cd /opt/orthanc/ \
  && git clone https://code.oak-tree.tech/oak-tree/medical-imaging/orthanc-sonador.git \
  && cd orthanc-sonador && git checkout $GIT_COMMIT_SHA \
  && git submodule update --init --recursive --remote

