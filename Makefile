MAKEFILE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))


MODELRUNNERCLIENTDIR = $(MAKEFILE_DIR)/projects/modelrunner_client

.PHONY: docs

docs:
	$(MAKE) -C $(MODELRUNNERCLIENTDIR) docs
	rm -rf docs
	mkdir -p docs/_build/html
	cp -a $(MODELRUNNERCLIENTDIR)/docs/_build/html docs/_build/html/sdk
