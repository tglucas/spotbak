#!/usr/bin/env bash
set -e
set -o pipefail

sudo apt update
sudo apt install -y --no-install-recommends \
    python3 \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-venv \
    python3-wheel
sudo update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# work around pip stupidity
python -m pip install --upgrade pip
# work around setuptools stupidity
python -m pip install --upgrade setuptools
# work around wheel stupidity
python -m pip install --upgrade wheel

# virtual-env updates

python -m venv --system-site-packages ./build/
. ./build/bin/activate
# work around timeouts to www.piwheels.org
export PIP_DEFAULT_TIMEOUT=60

# work around pip stupidity
python -m pip install --upgrade pip
# work around setuptools stupidity
python -m pip install --upgrade setuptools
# work around wheel stupidity
python -m pip install --upgrade wheel

# work around apt/pip stupidity
python -m pip install --upgrade -r "./requirements.txt"

deactivate
