#!/usr/bin/env python
from setuptools import setup, find_packages

setup(
    name="pybitcoin",
    version=0.1,
    description="Ultra-lightweight bitcoin client and library",
    author="Chris Pacia",
    author_email="ctpacia@gmail.com",
    license="MIT",
    url="http://github.com/cpacia/pybitcoin",
    packages=find_packages(),
    requires=["bitcoin", "dnspython"],
    install_requires=["dnspython>=1.12.0", "python-bitcoinlib>=0.5.0", "Twisted>=14.0.2"]
)
