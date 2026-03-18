from django.shortcuts import render

# Create your views here.

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.utils.translation import gettext as _
from django.utils import translation

from pathlib import Path
import subprocess
import zipfile
import time
import uuid
import re
import csv
import tempfile
import json
import os

from gumper import config as gcf
from gumper.gumper_client_web import main as gumper_main
from gama import utils as ut # views is run from package


DBG = False
DEFAULTS_TO_TRANSLATE = {
    "corpus_name": ["Unnamed corpus"],
    "doc_name": ["Untitled"],
    "doc_subtitle": ["—"],
    "author": ["Unknown"],
    "date": ["—"],
}

def clear_session(request):
    """
    Supprime les données d'analyse stockées en session et redirige vers la page index
    """
    try:
        del request.session['analysis_data']
    except KeyError:
        pass
    return redirect('gama:index')

def _handle_language(request):
    """
    Gestion des langues
    """

    # Initialise lang à None
    lang = None

    # Récupère les langues envoyées en POST (menu de langues)
    if request.method == "POST":
        lang = request.POST.get("language")

    # Si une langue est récupérée (=/ None) on active cette langue
    # (--> changement de langue d'une page via le menu de langues)
    if lang:
        translation.activate(lang)
        request.LANGUAGE_CODE = lang

    # Sinon (aucune langue passée en POST) on récupère la langue enregistrée par Django
    # (--> simple navigation ou rechargement d'une page)
    else:
        lang = translation.get_language()
        translation.activate(lang)

    # Renvoi la langue utilisée
    return lang


def _load_example_poems():
    """
    Charge le fichier JSON contenant des exemples de poèmes.
    """
    example_path = os.path.join(settings.BASE_DIR, "gama", "ext_data", "ex_poem.json")
    try:
        with open(example_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def index(request):
    """
    Page d'accueil de l'application.

    Étapes :
    1. Gére la langue via handle_language.
    2. Charge les poèmes d'exemple pour l'affichage.
    3. Récupère les données précédemment saisies en session
       (si l'utilisateur revient sur la page les champs restent pré-remplis).
    4. Passe les données au template index.html pour affichage.
    """
    # Gestion de la langue
    _handle_language(request)
    # Chargement des poèmes exemples
    example_poems = _load_example_poems()
    # Récupération des metadata depuis la session
    initial_data = request.session.get('analysis_data', {})
    # Rendu template html avec le context
    return render(request, "gama/index.html", {
            "example_poems": example_poems,
            "initial_data": initial_data,
    })

def _translate_if_default(value, key):
    """
    Traduit une valeur uniquement si elle correspond à une valeur par défaut
    définie dans DEFAULTS_TO_TRANSLATE.
    """
    if value in DEFAULTS_TO_TRANSLATE.get(key, []):
        return _(value)
    return value

def analysis_run(request):
    """
    Traite le POST d'une analyse de texte.

    Rôle :
    1. Vérifie et valide le texte soumis (taille, format des vers, etc.).
    2. Récupère et stocke les metadata dans la session.
       (utile pour conserver les valeurs si l'utilisateur change de langue)
    3. Crée un dossier unique pour l'analyse et écrit le texte en fichier.
    4. Lance le prétraitement via un script externe.
    5. Effectue l'analyse métrique via gumper_main et stocke :
       - scansion (pour affichage)
       - results_data (pour export)
    6. Redirige vers analysis_results pour afficher le résultat (PRG).

    Remarques :
    - Ne gère que le POST.
    - Les résultats sont conservés en session pour permettre changement de langue
      sans relancer l'analyse.
    """
    # Gestion de la langue via handle_language
    _handle_language(request)

    # Récupère et vérifie que le texte n'est pas vide, trop long, ou contient des vers trop longs
    if request.method == "POST":
        text = request.POST.get("text", "")
        text = ut._preprocess_poem_text(text)
        if not text:
            return redirect("gama:error", errtype="empty")
        if len(text) > 10000000004500:
            return redirect("gama:error", errtype="too_long")
        if any(len(line.strip()) > 200 for line in text.splitlines() if line.strip()):
            return redirect("gama:error", errtype="not_verse")

        # Récupération des métadonnées (or "valeur par défaut" si champ vide)
        corpus_name_key = request.POST.get("corpus_name") or "—"
        doc_name_key = request.POST.get("doc_name") or "—"
        doc_subtitle_key = request.POST.get("doc_subtitle") or "—"
        author_key = request.POST.get("author") or "Unknown"
        date_key = request.POST.get("date") or "—"

        # Stockage en session des metadata pour pouvoir changer la langue sur la page
        # (et éviter l'erreur et redirection 'empty' lors du changement)

        # session["analysis_data"] contains the text + metadata
        request.session['analysis_data'] = {
            "text": text,
            "corpus_name": corpus_name_key,
            "doc_name": doc_name_key,
            "doc_subtitle": doc_subtitle_key,
            "author": author_key,
            "date": date_key,
        }

        # ID unique et dossier de sortie
        request.session["curid"] = str(uuid.uuid4())[0:6]
        curid = request.session["curid"]
        out_dir = settings.IO_DIR / curid
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        with open(out_dir / "input.txt", encoding="utf8", mode="w") as f:
            f.write(text)

        try:
            subprocess.run(
                ["python", "../preprocessing/g2s_client_running_text.py",
                 str(out_dir / "input.txt"), "-p", "-d", "-n", "-s", "-b", "001", "-m"],
                check=True,
                cwd=settings.PREPRO_DIR,
            )

            # Analyse métrique
            orig_poem_path = out_dir / "input.txt"
            prepro_poem_path = out_dir / "out_001" / "input_pp_out_norm_spa_001.txt"

            # `scansion` is a list of HTML table rows, `results_data` is a list of dict for exporting
            # both are added to session
            scansion, results_data = gumper_main(gcf, orig_poem_path, prepro_poem_path)

            # old
            #request.session['analysis_result'] = "".join(scansion)

            # Stockage résultats de l'analyse en session
            # Pour pouvoir changer lg depuis la page de résultats (sans relancer l'analyse)

            request.session['analysis_result_desktop'] = "".join(scansion["desktop"])
            request.session['analysis_result_mobile'] = "".join(scansion["mobile"])
            # Stockage des résultats pour l'export au format tsv
            request.session['results_data'] = results_data

            #Redirection nouvelle view pour PRG
            return redirect("gama:analysis_show")

        # Si erreur lors de l'analyse
        except Exception as e:
            print(f"Unexpected error during analysis: {e}")
            # Redirection vers page error.html
            return redirect("gama:error", errtype="unexpected")
    return redirect("gama:index")

def analysis_show(request):
    """
    Affiche les résultats d'une analyse de texte.

    Rôle :
    1. Récupère depuis la session :
       - `analysis_data` (texte + métadonnées)
       - `analysis_result` (scansion du texte)
    2. Si les données sont absentes, redirige vers une page d'erreur.
    3. Traduit les métadonnées par défaut si besoin,
       sans toucher aux valeurs saisies par l'utilisateur.
    4. Passe les données au template analysis.html pour affichage.

    Remarques :
    - Gère uniquement le GET.
    - Permet de changer la langue sur la page résultats sans perdre le texte ou
      les métadonnées.
    - Compatible avec le principe PRG : l'analyse est séparée de l'affichage.
    """
    # Gestion de la langue
    _handle_language(request)
    # Récupération des données stockées en session
    # Permet d'afficher sans relancer l'analyse
    # `analysis_data` is the text + metadata
    analysis_data = request.session.get("analysis_data")

    # now have two variables for scansion results: desktop and mobile
    analysis_result_desktop  = request.session.get('analysis_result_desktop')
    analysis_result_mobile = request.session.get('analysis_result_mobile')

    # Si vide et qu'on ne peut rien afficher, redirection vers page erreur 'empty'
    if not analysis_data or not (analysis_result_desktop or analysis_result_mobile):
        return redirect("gama:error", errtype="empty")

    # Traduction des métadonnées uniquement si valeurs par défaut
    # (ne traduit pas des valeurs saisies par l'utilisateur)
    corpus_name = _translate_if_default(analysis_data.get("corpus_name", "Unnamed corpus"), "corpus_name")
    doc_name = _translate_if_default(analysis_data.get("doc_name", "Untitled"), "doc_name")
    doc_subtitle = _translate_if_default(analysis_data.get("doc_subtitle", "—"), "doc_subtitle")
    author = _translate_if_default(analysis_data.get("author", "Unknown"), "author")
    date = _translate_if_default(analysis_data.get("date", "—"), "date")

    context = {
        "text": analysis_data.get("text", ""),
        "corpus_name": corpus_name,
        "doc_name": doc_name,
        "doc_subtitle": doc_subtitle,
        "author": author,
        "date": date,
        "result_desktop": request.session.get('analysis_result_desktop', ""),
        "result_mobile": request.session.get('analysis_result_mobile', ""),
    }

    return render(request, "gama/analysis.html", context)

def error(request, errtype):
    """
    Gère l'affichage des erreurs côté utilisateur.

    Étapes :
    1. Active la langue de l'utilisateur via handle_language.
    2. Détermine le message d'erreur en fonction de `errtype` :
       - 'empty' : texte vide
       - 'too_long' : texte trop long
       - 'not_verse' : ligne trop longue / non conforme au format vers
       - autre : erreur inattendue
    3. Prépare le contexte pour le template de la page d'accueil,
       incluant des poèmes exemples pour que le sélecteur ne soit pas vide.
    4. Rendu du template approprié.
    """

    # Gestion de la langue
    _handle_language(request)
    # Gestion du message selon le type d'erreur
    if errtype == "empty":
        err_message = _("The input text cannot be empty.")
    elif errtype == "too_long":
        err_message = _("The input text is too long. Maximum length allowed: 4500 characters.")
    elif errtype == "not_verse":
        err_message = _("The input text does not seem to be in verse (lines too long?).")
    else:
        return render(request, "gama/error.html", {
            "message": _("An unexpected error occurred.")
        })

    # Contexte pour renvoi vers page d'accueil (index)
    # avec message d'erreur + rechargement des poèmes exemples
    example_poems = _load_example_poems()    # Nécessaire, sinon sélecteur vide sur page erreur
    context = {
        "error_message": err_message,
        "example_poems": example_poems,
    }
    # Rendu de la page d'accueil avec l'erreur
    return render(request, "gama/index.html", context)

def export_results(request):
    """
    Exporte les résultats d'une analyse sous forme de ZIP.

    Contenu du ZIP :
    - input.txt : texte brut
    - metadata.tsv : métadonnées (traduites si valeurs par défaut)
    - results.tsv : résultats de l'analyse métrique

    Étapes :
    1. Gestion de la langue.
    2. Récupération des données en session : texte, métadonnées, résultats, ID unique.
    3. Vérification que des résultats existent, sinon HTTP 400.
    4. Traduction conditionnelle des métadonnées par défaut.
    5. Création d'un répertoire temporaire pour générer les fichiers.
    6. Écriture des fichiers texte et TSV.
    7. Création d'une archive ZIP contenant ces fichiers.
    8. Envoi du ZIP en réponse HTTP avec header Content-Disposition.
    """
    # Gestion de la langue
    _handle_language(request)

    # Récupération des données depuis la session
    analysis_data = request.session.get("analysis_data")
    results_data = request.session.get("results_data")
    text = analysis_data.get("text", "") if analysis_data else ""
    curid = request.session.get("curid", "000000")

    if not results_data:
        return HttpResponse("No analysis results to export.", status=400)

    # Traduction des métadonnées uniquement si valeurs par défaut
    # (ne traduit pas des valeurs saisies par l'utilisateur)
    corpus_name_key = analysis_data.get("corpus_name", "Unnamed corpus")
    doc_name_key = analysis_data.get("doc_name", "Untitled")
    doc_subtitle_key = analysis_data.get("doc_subtitle", "—")
    author_key = analysis_data.get("author", "Unknown")
    date_key = analysis_data.get("date", "—")

    corpus_name = _translate_if_default(corpus_name_key, "corpus_name")
    doc_name = _translate_if_default(doc_name_key, "doc_name")
    doc_subtitle = _translate_if_default(doc_subtitle_key, "doc_subtitle")
    author = _translate_if_default(author_key, "author")
    date = _translate_if_default(date_key, "date")

    # Création d'un répertoire temporaire pour générer fichiers de sortie
    with tempfile.TemporaryDirectory() as tmpdir:
        # input.txt
        with open(f"{tmpdir}/input.txt", "w", encoding="utf-8") as f:
            f.write(text)

        # metadata.tsv
        with open(f"{tmpdir}/metadata.tsv", "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow([_("corpus_name"), _("title"), _("subtitle"), _("author"), _("date")])
            writer.writerow([corpus_name, doc_name, doc_subtitle, author,date])

        # results.tsv
        with open(f"{tmpdir}/results.tsv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "#", _("original_text"), _("preprocessing"),
                _("metrical_syllables"), _("stressed_syllables"), _("no_extra_rhythmic")
            ], delimiter="\t")
            writer.writeheader()
            for row in results_data:
                writer.writerow({
                    "#": row["line"],
                    _("original_text"): row["original_text"],
                    _("preprocessing"): row["preprocessing"],
                    _("metrical_syllables"): row["metrical_syllables"],
                    _("stressed_syllables"): row["stressed_syllables"],
                    _("no_extra_rhythmic"): row["no_extra_rhythmic"]
                })
        # Création du zip contenant les 3 fichiers
        zip_filename = f"results_scansion_{curid}.zip"
        zip_path = f"{tmpdir}/{zip_filename}"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.write(f"{tmpdir}/input.txt", "input.txt")
            zipf.write(f"{tmpdir}/metadata.tsv", "metadata.tsv")
            zipf.write(f"{tmpdir}/results.tsv", "results.tsv")

        # Lecture du zip + renvoi en réponse HTTP pour téléchargement
        with open(zip_path, "rb") as f:
            response = HttpResponse(f.read(), content_type="application/zip")
            response["Content-Disposition"] = f"attachment; filename={zip_filename}"
            return response

def about(request):
    """'About' page for each language."""
    # Gestion de la langue
    _handle_language(request)
    return render(request, f"gama/about/about_{request.LANGUAGE_CODE}.html")


def analysis_bulk(request):
    """
    Analyse par lot de plusieurs poèmes à partir d'un ZIP contenant des fichiers txt.

    Rôle :
    1. Gérer la langue via handle_language.
    2. Vérifier la présence d'un fichier ZIP uploadé.
    3. Décompresser le ZIP dans un dossier temporaire.
    4. Parcourir chaque fichier .txt et :
       - Lire le texte
       - Générer un ID unique pour l'analyse
       - Sauvegarder le texte brut
       - Prétraiter le texte via le script de preprocessing
       - Analyser le texte (scansion et métrique)
       - Générer un fichier TSV des résultats
    5. Regrouper tous les TSV générés dans un ZIP de sortie.
    6. Retourner le ZIP via une réponse HTTP avec header Content-Disposition
       pour téléchargement direct.

    Retour :
    - ZIP contenant les résultats TSV de tous les poèmes valides,
      ou HTTP 400 si aucun fichier ou aucun poème valide n'est fourni.
    """
    # Gestion de la langue
    _handle_language(request)

    # Vérification POST + fichier zip
    if request.method != 'POST' or 'zip_file' not in request.FILES:
        return JsonResponse({"error": _("No ZIP file uploaded or method not allowed.")}, status=400)
    uploaded_zip = request.FILES['zip_file']

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Sauvegarde temporaire du zip uploadé
            zip_path = os.path.join(tmpdir, 'uploaded.zip')
            with open(zip_path, 'wb') as f:
                for chunk in uploaded_zip.chunks():
                    f.write(chunk)

            # Extraction du zip
            extract_dir = os.path.join(tmpdir, 'extracted')
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            txt_files = [f for f in os.listdir(extract_dir) if f.lower().endswith('.txt')]
            # Vérification du nombre de fichiers
            if not txt_files:
                return JsonResponse({"error": _("No TXT files found in the ZIP.")}, status=400)
            if len(txt_files) > 1000000000:
                return JsonResponse({"error": _("Too many files in ZIP. Maximum allowed is 10.")}, status=400)
            result_paths = []
            errors = []
            too_long_files = []

            # Parcours des fichiers extraits
            for fname in os.listdir(extract_dir):
                input_path = os.path.join(extract_dir, fname)
                try:
                    # Lecture du texte
                    with open(input_path, "r", encoding="utf-8") as f:
                        text = f.read()

                    # Ignore les fichiers vides
                    if not text.strip():
                        continue
                    # Vérifie la taille du texte
                    if len(text) > 4500:
                        errors.append(f"File '{fname}' is too long. Maximum allowed is 4,500 characters.")
                        too_long_files.append(fname)
                        continue  # passe au fichier suivant

                    # ID et dossier de sortie
                    curid = str(uuid.uuid4())[:6]
                    out_dir = settings.IO_DIR / f"bulk_{curid}"
                    Path(out_dir).mkdir(parents=True, exist_ok=True)

                    # Sauvegarde texte brut
                    input_txt = out_dir / "input.txt"
                    with open(input_txt, "w", encoding="utf-8") as f:
                        f.write(text)

                    # Prétraitement
                    subprocess.run(
                        ["python", "../preprocessing/g2s_client_running_text.py",
                        str(input_txt), "-p", "-d", "-n", "-s", "-b", "001", "-m"],
                        check=True,
                        cwd=settings.PREPRO_DIR,
                    )

                    # Analyse
                    orig_poem_path = input_txt
                    prepro_poem_path = out_dir / "out_001" / "input_pp_out_norm_spa_001.txt"
                    scansion, results_data = gumper_main(gcf, orig_poem_path, prepro_poem_path)

                    # Création du fichier TSV
                    result_name = f"{Path(fname).stem}_results.tsv"
                    result_path = os.path.join(tmpdir, result_name)
                    with open(result_path, "w", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=[
                            "#", _("original_text"), _("preprocessing"),
                            _("metrical_syllables"), _("stressed_syllables"), _("no_extra_rhythmic")
                        ], delimiter="\t")
                        writer.writeheader()
                        for row in results_data:
                            writer.writerow({
                                "#": row["line"],
                                _("original_text"): row["original_text"],
                                _("preprocessing"): row["preprocessing"],
                                _("metrical_syllables"): row["metrical_syllables"],
                                _("stressed_syllables"): row["stressed_syllables"],
                                _("no_extra_rhythmic"): row["no_extra_rhythmic"]
                            })

                    result_paths.append((result_name, result_path))

                except Exception as e:
                    print(f"Error with {fname}: {e}")
                    errors.append(f"{fname}: {str(e)}")
                    continue

            # Si des erreurs existent, on crée errors.txt
            if errors:
                error_path = os.path.join(tmpdir, "errors.txt")
                with open(error_path, "w", encoding="utf-8") as f:
                    for line in errors:
                        f.write(line + "\n")
                result_paths.append(("errors.txt", error_path))

            # Création du zip de sortie avec ID unique
            curid = str(uuid.uuid4())[:6]
            original_name = uploaded_zip.name
            base_name = original_name.rsplit('.', 1)[0]
            output_zip_name = f"{base_name}_results_{curid}.zip"
            with zipfile.ZipFile(output_zip_name, 'w') as out_zip:
                for name, path in result_paths:
                    out_zip.write(path, arcname=name)

            # Réponse HTTP pour téléchargement
            with open(output_zip_name, 'rb') as f:
                response = HttpResponse(f.read(), content_type='application/zip')
                response['Content-Disposition'] = f'attachment; filename="{output_zip_name}"'
                # Si erreurs lors de l'analyse
                if errors:
                    if too_long_files and len(errors) == len(too_long_files):
                        # Cas 1 : uniquement des fichiers trop longs
                        response_msg = (
                            _("Files above max allowed characters (4,500) were not analyzed. See error log in zip.")
                        )
                    elif too_long_files:
                        # Cas 2 : mélange erreurs d'analyse ET fichiers trop longs
                        response_msg = (
                            _("Some files failed and files above max allowed characters (4,500) were not analyzed. "
                              "See error log in zip.")
                        )
                    else:
                        # Cas 3 : uniquement erreurs d'analyse
                        response_msg = _("Some files failed. See error log in zip.")
                    response['X-Analysis-Status'] = response_msg
                return response

    except zipfile.BadZipFile:
        return JsonResponse({"error": _("Invalid ZIP file.")}, status=400)
    except Exception as e:
        print(e)
        return JsonResponse({"error": _("An unexpected error occurred.")}, status=400)
