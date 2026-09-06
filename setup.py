from setuptools import setup, find_packages

setup(
    name="specter-core",
    version="1.1.0",
    packages=find_packages(include=["Core", "Core.*"]),
    python_requires=">=3.10",
    install_requires=[],
    entry_points={
        "console_scripts": [
            "specter-cli=Core.specter_cli:main",
            "specter-gateway=Core.unified_inference_gateway:main_cli",
            "specter-supervisor=Core.specter_supervisor_247:main",
        ],
    },
)
