INTRODUCTION
============

SAFE (or Spatial Analysis of Functional Enrichment) is an automated network annotation algorithm. Given a biological network and a set of functional groups or quantitative features of interest, SAFE performs local enrichment analysis to determine which regions of the network are over-represented for each group or feature. SAFE visualizes the network and maps the detected enrichments onto the network.

SAFE was originally implemented in MATLAB and stored at  <https://bitbucket.org/abarysh/safe/>. However, as of early 2017, the MATLAB implementation is only maintained for historical reasons. All new work related to SAFE has been moved to Python and this repository. 

GETTING STARTED
===============

The list of package requirements is provided in `extras/requirements.txt` in this repository. We recommend setting up a virtual environment and installing all the required packages via pip:
```
virtualenv ~/virtualenvs/safepy/
source ~/virtualenvs/safepy/bin/activate
pip install -r extras/requirements.txt
```
After the installation is complete, we recommend running a "hello world" SAFE analysis using the Jupyter notebook at `examples/Usage_examples.ipynb`.

HELP
====

Please direct all questions/comments to Anastasia Baryshnikova (<abaryshnikova@calicolabs.com>).

The main repository for this code is at <https://github.com/baryshnikova-lab/safepy>. Please subscribe to the repository to receive live updates about new code releases and bug reports.


HOW TO CITE
==========

The manuscript describing SAFE and its applications is available at:

> Baryshnikova, A. (2016). Systematic Functional Annotation and Visualization of Biological Networks. Cell Systems. <http://doi.org/10.1016/j.cels.2016.04.014>