# GAMA web: Galician Metrical Analyzer

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.17187974.svg)](https://doi.org/10.5281/zenodo.17187974)


This Django app is the web version of GAMA, the Galician Metrical Analyzer. It provides a metrical analysis of poetry in Galician. It is deployed at https://prf2.org/gama/

The scansion algorithm is [Jumper](https://github.com/grmarco/jumper/blob/master/jumper.py) (Marco Remón & Gonzalo, 2021). This is included in module `gumper/gumper.py` (the *g* is for *Galician*). Jumper was created for Spanish, but we adapted its data to work with Galician.

We also added a preprocessing to handle non-normative spelling in Galician, this is useful for historical texts predating an orthographic norm. Most processing time is due to this preprocessing; scansion with Jumper is very fast.

More details in the app's *About* page: in [English](https://prf2.org/en/gama/about/), [Galician](https://prf2.org/gl/gama/about/) and [French](https://prf2.org/fr/gama/about/) so far.

## Citation

Moreau, Pauline & Ruiz Fabo, Pablo. (2025). GAMA web: Interface for the metrical analysis of Galician poetry. CiTIUS - Universidade de Santiago de Compostela.

To cite the Jumper library (unrelated to our project, but that we used in our implementation):

Marco Remón, G., & Gonzalo, J. (2021). Escansión automática de poesía española sin silabación. *Procesamiento del Lenguaje Natural*, 66, pp. 77-87. Available at [SEPLN](http://journal.sepln.org/sepln/ojs/ojs/index.php/pln/article/view/6615)