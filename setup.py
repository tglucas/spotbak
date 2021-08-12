import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="spotbak",
    version="0.0.1",
    author="Tai Lucas",
    author_email="tglucas@gmail.com",
    description="Spotify account backup tool.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/tglucas/spotbak",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "lib"},
    packages=setuptools.find_packages(where="lib"),
    python_requires=">=3.6",
)
