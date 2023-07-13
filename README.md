# Sonador Orthanc Plugin


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
