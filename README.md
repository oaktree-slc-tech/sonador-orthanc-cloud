# Sonador Orthanc Plugin


### Tests
Unit tests live in `tests/` and cover the Kafka producer configuration builder — the protocol and
credential matrix, every validation failure, and the log redaction of secrets.

They do **not** require the plugin's runtime: the Orthanc SDK and the Sonador imaging stack are
stubbed by `tests/conftest.py`, so the suite runs against a bare checkout with nothing installed but
pytest.

```bash
pip install -r requirements-dev.txt
python -m pytest
```


### Kafka Transport Security
The Kafka producer supports TLS and SASL through an optional `security` block on
`Sonador.Kafka` in the Orthanc JSON configuration:

```json
"Sonador": {
  "Kafka": {
    "topic": "orthanc-index",
    "servers": ["kafka:9093"],
    "security": {
      "protocol": "SASL_SSL",
      "ssl": { "ca": "/etc/orthanc/certs/ca.pem", "verifyHostname": true },
      "sasl": {
        "mechanism": "SCRAM-SHA-512",
        "username": "orthanc",
        "passwordFile": "/run/secrets/kafka_sasl_password"
      }
    }
  }
}
```

Omit the block entirely and the producer is built with `bootstrap.servers` alone, exactly as before
the option existed — an existing PLAINTEXT deployment upgrades with no configuration change.

**Supply credentials by file reference (`passwordFile`, `keyPasswordFile`), not inline.** The Orthanc
JSON is a committed file under compose and a ConfigMap — not a Secret — under Kubernetes, so an
inline password is a secret in version control. Where both forms are present the file wins and a
warning is logged naming the key, never the value.

A configuration that cannot work fails at plugin startup with a message naming the offending key,
including a certificate or secret path that is missing or unreadable. Full key reference, the
librdkafka property each key maps to, and the validation rules are documented in [Data Streaming with
Kafka](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env/-/wikis/dev.kafka).


### Databse Setup
The Orthanc/Sonador plugin uses Alembic to manage a subset of tables which manage the resource cache, procedure worklists, and user access controls. These tables are separate from those managed by the PostgreSQL plugin of Orthanc and must be created by running an Alembic migration. Quickstart:

* (Production) Create an [init container (for Kubernetes)](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/) to run the Alembic commands (see below).
* (Development) Use `docker exec -it <container-name>` to launch a prompt within the container to run migration commands.

Apply all database migrations:

```bash
# Switch to plugin directory in the container
cd /opt/orthanc/orthanc-sonador

# Run Alembic migrations
alembic upgrade head
```
