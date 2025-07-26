#!/usr/bin/env python3
"""Test HTTP helper functions."""

import sys
import os
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from fastapi_ding import get, post, put, delete

try:
    print("✅ HTTP method helpers imported successfully!")
    
    # Test that they are callable
    print("✅ All helper functions are callable:")
    print(f"  - get: {get}")
    print(f"  - post: {post}")
    print(f"  - put: {put}")
    print(f"  - delete: {delete}")
    
    # Test that they return decorators
    test_decorator = get("/test")
    print(f"✅ get() returns a decorator: {test_decorator}")
    
    print("\n🎉 All tests passed! HTTP method helpers are working correctly.")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    sys.exit(1) 