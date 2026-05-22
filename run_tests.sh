#!/bin/sh

# Backend
.venv/bin/pytest tests/test_backend.py -v

# Frontend
cd tests && npm test
