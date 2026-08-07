MAKEFILE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

SPHINXOPTS    = --fresh-env
SPHINXBUILD   = sphinx-build
SPHINXAPIDOC  = sphinx-apidoc
SOURCEDIR     = $(MAKEFILE_DIR)/docs
BUILDDIR      = $(MAKEFILE_DIR)/docs/_build
PROJECTDIR    = $(MAKEFILE_DIR)/src/modelrunner_ai

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help Makefile clean apidoc html docs

clean:
	rm -rf $(BUILDDIR)/*

apidoc:
	$(SPHINXAPIDOC) -f -o "$(SOURCEDIR)" "$(PROJECTDIR)"

html:
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# clean first: --fresh-env rebuilds the doctree cache but leaves stale output
# files behind, so pages for renamed or removed modules would survive forever.
docs: clean apidoc html
