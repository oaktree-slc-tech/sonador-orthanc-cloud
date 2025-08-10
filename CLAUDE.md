# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the Sonador Orthanc Plugin, a Python-based plugin that integrates with the Orthanc DICOM server to provide enhanced medical imaging capabilities. The plugin extends Orthanc with features like user authentication, DICOM caching, worklist management, and DICOM tag handling.

## Development Commands

### Database Management
```bash
# Apply all database migrations (run inside container)
cd /opt/orthanc/orthanc-sonador
alembic upgrade head

# Create new migration
alembic revision -m "description"
```

### Docker Development
```bash
# Build main container
docker build -t orthanc-sonador .

# Build GCP variant
docker build -f Dockerfile.gcp -t orthanc-sonador-gcp .

# Build local development
docker build -f Dockerfile.local -t orthanc-sonador-local .
```

## Project Architecture

### Core Components

- **sonador-plugin.py**: Main plugin entry point that initializes Orthanc integration, Kafka producers, and database connections
- **sonador_orthanc/**: Main Python package containing all plugin functionality
- **orthanc-sonador-common/**: Shared utilities and base classes (git submodule)
- **alembic/**: Database migration management for PostgreSQL tables

### Key Modules

- **auth/**: User authentication and authorization
- **cache/**: DICOM resource caching system with PostgreSQL backend
- **db/**: Database models and ORM layer using SQLAlchemy
- **dcmquery/**: DICOM query operations (Patient, Study, Series)
- **kafka/**: Event streaming integration
- **validation/**: Input validation for various endpoints
- **web/**: REST API endpoints and views
- **worklist/**: DICOM worklist management

### Database Schema

Uses Alembic migrations to manage tables separate from Orthanc's core PostgreSQL plugin:
- Resource cache tables
- User access control tables
- Procedure worklists
- DICOM tag management
- Comment systems

### Dependencies

- **Orthanc Python API**: Core integration with Orthanc server
- **SQLAlchemy 1.4.39**: Database ORM
- **Alembic 1.8.1**: Database migrations
- **confluent-kafka**: Event streaming
- **sonador**: Core medical imaging API client
- **psycopg2-binary**: PostgreSQL adapter

### Configuration

Plugin requires configuration sections in Orthanc's JSON config:
- `Sonador`: API connection and authentication
- `PostgreSQL`: Database connection (required)
- `DicomWeb`: DICOMweb endpoint configuration
- Private DICOM tag definitions

### Key Design Patterns

- Uses Orthanc Python plugin callbacks for DICOM resource events
- Manager pattern for server lifecycle and recurring tasks
- Factory pattern for imaging server instances
- Event-driven architecture with Kafka integration
- Database session management with SQLAlchemy