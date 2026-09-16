from setuptools import setup, find_packages

setup(
    name="specter-core",
    version="5.1.0",
    packages=find_packages(include=["Core", "Core.*"]),
    python_requires=">=3.10",
    install_requires=[],
    entry_points={
        "console_scripts": [
            "specter-cli=Core.specter_cli:main",
            "specter-gateway=Core.unified_inference_gateway:main_cli",
            "specter-supervisor=Core.specter_supervisor_247:main",
            "specter-terminal=Core.specter_terminal:main",
            "specter-mcp=Core.specter_mcp_server:main",
            "specter-dispatcher=Core.specter_task_dispatcher:main",
            "specter-righthand=Core.right_hand_orchestrator:main",
            "specter-autohealer=Core.auto_healer:main",
        ],
    },
)
