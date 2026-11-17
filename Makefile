UV ?= uv
PYTHON_VERSION ?= 3.11
# Run from this checkout, never an installed ub.
RUN = $(UV) run -q --no-project --python $(PYTHON_VERSION) --with-editable .

.PHONY: help install test gate build build-native release check-upstreams

help:
	@printf '%s\n' \
	    'make install          Install the ub command with uv' \
	    'make test             Run the test suite' \
	    'make gate             Run ub check on src/ with this checkout; CI runs it on every PR' \
	    'make build            Build the wheel in dist/' \
	    'make build-native     Build the release archive for this host in dist/' \
	    'make release [BUMP=patch|minor|major]  Tag the next version on main and push it; CI publishes the release' \
	    'make check-upstreams  Compare pinned engines with their latest upstream commits'

install:
	$(UV) tool install . --python $(PYTHON_VERSION)

test:
	$(RUN) python -m unittest discover -s tests -v

# scripts/ is release tooling, out of the gate's scope; see README.
gate:
	$(RUN) ub check -s src --test-command '$(RUN) python -m unittest discover -s tests -q'

build:
	$(UV) build --wheel --python $(PYTHON_VERSION)

check-upstreams:
	$(UV) run --no-project --python $(PYTHON_VERSION) python scripts/check_upstreams.py

build-native:
	sh scripts/build-release.sh

release:
	BUMP=$(BUMP) sh scripts/release.sh
