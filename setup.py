from setuptools import setup, find_packages

setup(
    name="testbed",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "requests",
        "pydantic",
        "jinja2",
        "kubernetes"
    ],
)