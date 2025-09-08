"""Test query modifiers configuration."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Mapped
from unittest.mock import Mock, patch

import pydantic
import sqlalchemy
from sqlalchemy import Select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.datastructures import QueryParams

from fastapi_restly._query_modifiers_config import (
    QueryModifierVersion,
    set_query_modifier_version,
    get_query_modifier_version,
    get_query_modifier_interface,
    get_query_param_schema_creator,
    apply_query_modifiers,
    create_query_param_schema,
)
from fastapi_restly._sqlbase import Base


class TestModel(Base):
    __tablename__ = "test_model_config"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()


class TestSchema(pydantic.BaseModel):
    name: str


@pytest.fixture
def select_query():
    return sqlalchemy.select(TestModel)


@pytest.fixture
def query_params():
    return QueryParams({"name": "test"})


class TestQueryModifierVersion:
    def test_query_modifier_version_enum(self):
        """Test that the enum has the expected values."""
        assert QueryModifierVersion.V1 == QueryModifierVersion("v1")
        assert QueryModifierVersion.V2 == QueryModifierVersion("v2")
        assert len(QueryModifierVersion) == 2


class TestConfigurationFunctions:
    def test_set_and_get_query_modifier_version(self):
        """Test setting and getting the query modifier version."""
        # Reset to V1 to ensure we start with the expected default
        set_query_modifier_version(QueryModifierVersion.V1)
        
        # Default should be V1
        assert get_query_modifier_version() == QueryModifierVersion.V1
        
        # Set to V2
        set_query_modifier_version(QueryModifierVersion.V2)
        assert get_query_modifier_version() == QueryModifierVersion.V2
        
        # Set back to V1
        set_query_modifier_version(QueryModifierVersion.V1)
        assert get_query_modifier_version() == QueryModifierVersion.V1

    def test_get_query_modifier_interface_v1(self):
        """Test getting V1 interface."""
        set_query_modifier_version(QueryModifierVersion.V1)
        interface = get_query_modifier_interface()
        
        # Should be V1Interface
        assert "V1Interface" in interface.__class__.__name__

    def test_get_query_modifier_interface_v2(self):
        """Test getting V2 interface."""
        set_query_modifier_version(QueryModifierVersion.V2)
        interface = get_query_modifier_interface()
        
        # Should be V2Interface
        assert "V2Interface" in interface.__class__.__name__

    def test_get_query_param_schema_creator_v1(self):
        """Test getting V1 schema creator."""
        set_query_modifier_version(QueryModifierVersion.V1)
        creator = get_query_param_schema_creator()
        
        # Should be the V1 creator function
        assert "create_query_param_schema" in creator.__name__

    def test_get_query_param_schema_creator_v2(self):
        """Test getting V2 schema creator."""
        set_query_modifier_version(QueryModifierVersion.V2)
        creator = get_query_param_schema_creator()
        
        # Should be the V2 creator function
        assert "create_query_param_schema_v2" in creator.__name__


class TestConvenienceFunctions:
    def test_apply_query_modifiers_v1(self, select_query, query_params):
        """Test apply_query_modifiers with V1 version."""
        set_query_modifier_version(QueryModifierVersion.V1)
        
        with patch('fastapi_restly._query_modifiers_config.get_query_modifier_interface') as mock_get_interface:
            mock_interface = Mock()
            mock_get_interface.return_value = mock_interface
            
            apply_query_modifiers(query_params, select_query, TestModel, TestSchema)
            
            mock_interface.apply_query_modifiers.assert_called_once_with(
                query_params, select_query, TestModel, TestSchema
            )

    def test_apply_query_modifiers_v2(self, select_query, query_params):
        """Test apply_query_modifiers with V2 version."""
        set_query_modifier_version(QueryModifierVersion.V2)
        
        with patch('fastapi_restly._query_modifiers_config.get_query_modifier_interface') as mock_get_interface:
            mock_interface = Mock()
            mock_get_interface.return_value = mock_interface
            
            apply_query_modifiers(query_params, select_query, TestModel, TestSchema)
            
            mock_interface.apply_query_modifiers.assert_called_once_with(
                query_params, select_query, TestModel, TestSchema
            )

    def test_create_query_param_schema_v1(self):
        """Test create_query_param_schema with V1 version."""
        set_query_modifier_version(QueryModifierVersion.V1)
        
        with patch('fastapi_restly._query_modifiers_config.get_query_param_schema_creator') as mock_get_creator:
            mock_creator = Mock()
            mock_get_creator.return_value = mock_creator
            
            create_query_param_schema(TestSchema)
            
            mock_creator.assert_called_once_with(TestSchema)

    def test_create_query_param_schema_v2(self):
        """Test create_query_param_schema with V2 version."""
        set_query_modifier_version(QueryModifierVersion.V2)
        
        with patch('fastapi_restly._query_modifiers_config.get_query_param_schema_creator') as mock_get_creator:
            mock_creator = Mock()
            mock_get_creator.return_value = mock_creator
            
            create_query_param_schema(TestSchema)
            
            mock_creator.assert_called_once_with(TestSchema)


class TestIntegration:
    def test_v1_integration(self, select_query, query_params):
        """Test V1 integration with actual query modifiers."""
        set_query_modifier_version(QueryModifierVersion.V1)
        
        # This should work with the actual V1 implementation
        result = apply_query_modifiers(query_params, select_query, TestModel, TestSchema)
        
        # Should return a Select object
        assert isinstance(result, Select)

    def test_v2_integration(self, select_query, query_params):
        """Test V2 integration with actual query modifiers."""
        set_query_modifier_version(QueryModifierVersion.V2)
        
        # This should work with the actual V2 implementation
        result = apply_query_modifiers(query_params, select_query, TestModel, TestSchema)
        
        # Should return a Select object
        assert isinstance(result, Select)

    def test_schema_creation_v1(self):
        """Test schema creation with V1 version."""
        set_query_modifier_version(QueryModifierVersion.V1)
        
        schema = create_query_param_schema(TestSchema)
        
        # Should create a schema with V1 fields
        assert "limit" in schema.model_fields
        assert "offset" in schema.model_fields
        assert "sort" in schema.model_fields

    def test_schema_creation_v2(self):
        """Test schema creation with V2 version."""
        set_query_modifier_version(QueryModifierVersion.V2)
        
        schema = create_query_param_schema(TestSchema)
        
        # Should create a schema with V2 fields
        assert "page" in schema.model_fields
        assert "page_size" in schema.model_fields
        assert "order_by" in schema.model_fields
        assert "name" in schema.model_fields 