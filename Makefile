UV ?= uv
PYTHON_VERSION ?= 3.11

.PHONY: help install test build build-native check-upstreams

help:
	@printf '%s\n' \
	    'make install          Install the ub command with uv' \
	    'make test             Run the test suite' \
	    'make build            Build the wheel in dist/' \
	    'make build-native     Build the release archive for this host in dist/' \
	    'make check-upstreams  Compare pinned engines with their latest upstream commits'

install:
	$(UV) tool install . --python $(PYTHON_VERSION)

test:
	$(UV) run --no-project --python $(PYTHON_VERSION) --with-editable . python -m unittest discover -s tests -v

build:
	$(UV) build --wheel --python $(PYTHON_VERSION)

check-upstreams:
	$(UV) run --no-project --python $(PYTHON_VERSION) python scripts/check_upstreams.py

build-native:
	sh scripts/build-release.sh
