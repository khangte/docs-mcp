"""샘플 OpenAPI 문서."""

from __future__ import annotations

import json

OPENAPI_3_DOC: dict = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore API", "version": "1.0.0"},
    "paths": {
        "/pet": {
            "post": {
                "operationId": "addPet",
                "summary": "Add a new pet to the store",
                "description": "Create a new pet",
                "tags": ["pet"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Pet"}
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Pet"}
                            }
                        },
                    },
                    "400": {"description": "invalid"},
                },
            }
        },
        "/pet/{petId}": {
            "get": {
                "operationId": "getPetById",
                "summary": "find pet by id",
                "description": "Returns a single pet",
                "tags": ["pet"],
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "example": 42},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Pet"}
                            }
                        },
                    },
                    "404": {"description": "not found"},
                },
            },
            "delete": {
                "operationId": "deletePet",
                "summary": "Deletes a pet",
                "description": "delete",
                "tags": ["pet"],
                "parameters": [
                    {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"204": {"description": "no content"}},
            },
        },
        "/user": {
            "post": {
                "operationId": "createUser",
                "summary": "Create user",
                "description": "Create a new user",
                "tags": ["user"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/User"}
                        }
                    },
                },
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string", "example": "doggie"},
                    "status": {"type": "string"},
                },
                "required": ["name"],
            },
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "username": {"type": "string"},
                    "email": {"type": "string"},
                },
            },
        }
    },
}


SWAGGER_2_DOC: dict = {
    "swagger": "2.0",
    "info": {"title": "Legacy API", "version": "0.9.0"},
    "consumes": ["application/json"],
    "produces": ["application/json"],
    "paths": {
        "/items": {
            "post": {
                "operationId": "createItem",
                "summary": "Create item",
                "parameters": [
                    {
                        "name": "body",
                        "in": "body",
                        "required": True,
                        "schema": {"$ref": "#/definitions/Item"},
                    }
                ],
                "responses": {
                    "201": {
                        "description": "created",
                        "schema": {"$ref": "#/definitions/Item"},
                    }
                },
            }
        },
        "/items/{id}": {
            "get": {
                "operationId": "getItem",
                "summary": "Get item",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "type": "integer"}
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "schema": {"$ref": "#/definitions/Item"},
                    }
                },
            }
        },
    },
    "definitions": {
        "Item": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
            },
        }
    },
}


def openapi_3_json() -> str:
    return json.dumps(OPENAPI_3_DOC)


def swagger_2_json() -> str:
    return json.dumps(SWAGGER_2_DOC)
