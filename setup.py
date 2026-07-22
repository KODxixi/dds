from setuptools import setup, find_packages

setup(
    name="dds-v2",
    version="2.0.0",
    description="DDS v2 - 数据驱动的地产决策报告引擎",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.8",
    install_requires=[
        "pydantic>=2.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
        ],
    },
)
