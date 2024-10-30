from setuptools import setup, find_packages

setup(
    name="moatless-testbeds",
    version="0.0.1",
    author="Albert Örwall",
    author_email="albert@moatless.ai",
    description="Run testbeds as isolated pods in a Kubernetes cluster",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/aorwall/moatless-testbeds",
    packages=find_packages(),
    install_requires=[
        "requests",
        "pydantic",
        "typing-extensions",
        "datasets"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.9",
)
