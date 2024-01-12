# syntax = docker/dockerfile:experimental
FROM oaktreetech/orthanc-s3:latest
ARG CI_COMMIT_SHA
ARG CLI_CI_COMMIT_SHA

# Install Python Requests Module and Other Dependencies
RUN apt-get update && apt-get install -y git build-essential python3-requests libtiff5-dev libjpeg-dev zlib1g-dev \
  libfreetype6-dev liblcms2-dev libwebp-dev libharfbuzz-dev libfribidi-dev librdkafka-dev \
  tcl8.6-dev tk8.6-dev python3-tk

# Install Sonador Python Plugin
RUN --mount=type=secret,id=auto-devops-build-secrets . /run/secrets/auto-devops-build-secrets \
  && mkdir -p /opt/orthanc/ \
  && export CI_COMMIT_SHA=${CI_COMMIT_SHA:-master} \
  && echo "Build container for Sonador/Orthanc Plugin $CI_COMMIT_SHA" \
  && cd /opt/orthanc/ \
  && git clone https://code.oak-tree.tech/oak-tree/medical-imaging/orthanc-sonador.git \
  && cd orthanc-sonador && git checkout $CI_COMMIT_SHA \
  && git submodule update --init --recursive --remote \
  && pip3 install --upgrade pip \
  && pip3 install --timeout 300 -r requirements.txt

# Install Sonador CLI Client
RUN cd /opt/ \
  && export CLI_CI_COMMIT_SHA=${CLI_CI_COMMIT_SHA:-master} \
  && echo "Checkout Sonador CLI $CLI_CI_COMMIT_SHA" \
  && git clone https://code.oak-tree.tech/oak-tree/medical-imaging/sonador-cli.git \
  && cd sonador-cli && git checkout $CLI_CI_COMMIT_SHA \
  && git submodule update --init --recursive --remote \
  && pip install --timeout 30 -r requirements.txt