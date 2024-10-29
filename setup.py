from setuptools import setup, find_packages

setup(
    name="moatless-testbeds",
    version="0.0.1",
    packages=find_packages(),
    install_requires=[
        "requests",
        "pydantic",
        "typing-extensions",
        "datasets"
    ],
    python_requires=">=3.9",
)
