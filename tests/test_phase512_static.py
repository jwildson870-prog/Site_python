"""Revisão estática da fase 5.12.

Não importa o Flask: pode ser executado mesmo em um ambiente sem dependências
instaladas e verifica integridade estrutural básica do projeto.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def discover_endpoints():
    endpoints = {"static", "home"}
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in {"route", "get", "post", "put", "delete", "patch"}:
                    continue
                if isinstance(func.value, ast.Name) and func.value.id.endswith("_bp"):
                    endpoints.add(f"{func.value.id[:-3]}.{node.name}")
                elif isinstance(func.value, ast.Name) and func.value.id == "app":
                    endpoints.add(node.name)
    return endpoints


def test_python_files_compile():
    for path in APP.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8", errors="ignore"))


def test_template_url_for_endpoints_exist():
    endpoints = discover_endpoints()
    missing = []
    pattern = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")
    for path in (APP / "templates").rglob("*.html"):
        for endpoint in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
            if endpoint not in endpoints:
                missing.append(f"{path.relative_to(ROOT)} -> {endpoint}")
    assert not missing, "Endpoints ausentes: " + "; ".join(missing)


def test_pwa_core_files_exist():
    for relative in (
        "app/static/manifest.json",
        "app/static/service-worker.js",
        "app/static/js/pwa.js",
        "app/static/offline.html",
    ):
        assert (ROOT / relative).is_file(), relative


def test_upload_limit_uses_runtime_configuration():
    source = (APP / 'admin' / 'routes.py').read_text(encoding='utf-8')
    assert "current_app.config.get('MAX_CONTENT_LENGTH'" in source
    assert 'MAX_UPLOAD = 25 * 1024 * 1024' not in source.replace('DEFAULT_MAX_UPLOAD = 25 * 1024 * 1024', '')


def test_storage_errors_do_not_expose_raw_provider_body():
    source = (APP / 'storage.py').read_text(encoding='utf-8')
    assert "response.text or ''" not in source
    assert 'technical_body' in source
