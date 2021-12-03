import fire
import requests
import sys
import os
import json
import subprocess
import logging
import shutil
import traceback
import dill
import pickle # noqa
import itertools
import pandas
import datetime 

from git import Repo
from dataclasses import asdict
from medcat.cli.config import get_auth_environment_vars, get_git_api_request_url, get_git_api_upload_url, get_git_default_headers, get_git_download_headers

from medcat.cli.global_settings import DEFEAULT_DATETIME_FORMAT
from medcat.cli.system_utils import copy_model_files_to_folder, create_model_folder, create_new_base_repository, get_downloaded_local_model_folder, get_local_model_storage_path, get_medcat_package_version, get_model_binary_file_extension, get_permitted_push_file_list, get_permitted_versioned_files, is_input_valid, load_file_from_model_storage, prompt_statement, sanitize_input

from .download import get_all_available_model_tags
from .modeltagdata import ModelTagData

pandas.set_option('display.max_colwidth', 400)
pandas.set_option('display.max_columns', 10)


def verify_model_package(request_url, headers, full_model_tag_name):

    available_model_tags = get_all_available_model_tags(request_url, headers)
    found_matching_tags = [tag_name for tag_name in available_model_tags if full_model_tag_name in tag_name] 

    found_model_folders = []
    root, subdirs, files = next(os.walk(get_local_model_storage_path()))

    for subdir in subdirs:
        if subdir in available_model_tags:
            found_model_folders.append(subdir)

    found_matching_folders = [dir_name for dir_name in found_model_folders if full_model_tag_name in dir_name] 

    if not found_matching_folders and found_matching_tags:
        logging.info("NO model named " + "\033[1m" + full_model_tag_name + "\033[0m" + " found on this machine, please download it...")
        return True

    return False


def select_model_package_and_name(model_name, previous_model_tag_data=False, predicted_version="1.0", stat_comparison_data=None):
    """
        Allows the user to select the model's name according to previous model history.
        :return model_name, is_new_release: model name and bool if its a new release 
    """

    is_new_release = False
    model_prefix = model_name

    different_organisation_base = False

    if stat_comparison_data is not None:
        print("Changes from previous versions:")
        stats_dataframe = pandas.DataFrame.from_dict(stat_comparison_data["stat_differences"])
        print(stats_dataframe)

        is_actual_new_release = stat_comparison_data["is_actual_new_release"]
        is_stat_improvement = stat_comparison_data["is_stat_improvement"]

        if is_actual_new_release:
            print("This model should be tagged as a new release as there are a different number of concepts detected in the new version compared to the previous, example:")
            for k,v in stat_comparison_data["stat_differences"]["cdb.dat"]["current"].items():
                if len(v) > 3:
                    print(k, v[0:3], ".....")
                else:
                    print(k, v, ".....")

        if is_stat_improvement == "NONE":
            print("No performance improvement detected... check the scores...")
        elif is_stat_improvement:
            print("This model should be tagged as an improvement as there are score improvements, example:")
        elif not is_stat_improvement:
            print("Poor performance detected... check the scores...")

        for k,v in stat_comparison_data["stat_differences"]["MedCAT_Export.json"]["current"].items():
            print(k, v, " over the last version.")

    if previous_model_tag_data is not False:
        print("\n")
        print("The model you want to package is based on the following model:" + "\033[1m" + previous_model_tag_data.model_name + "-" + previous_model_tag_data.version + "\033[0m" + ".")
        if model_name == "":
            model_name = previous_model_tag_data.model_name

        if get_auth_environment_vars()["git_organisation_name"] != previous_model_tag_data.organisation_name:
            different_organisation_base = True
            model_prefix = get_auth_environment_vars()["git_organisation_name"] + "-" + model_name
            print("This model has been created by a different organisation, the new models produced will have the the organisation name as a prefix to tag: ",  "\033[1m" + get_auth_environment_vars()["git_organisation_name"] + "-" + "<new_model_name>" + "\033[0m")
        else:
            model_prefix = model_name

        previous_model_name = "\033[1m" + previous_model_tag_data.model_name + "-" + previous_model_tag_data.version + "\033[0m"

    if model_name != "":
        print("Please use only alphabet characters (a-Z, A-Z), no numbers and no symbols accepted besides underscore '_' . Invalid characters will be removed.")

        while True:
            if not is_input_valid(model_name):
                print("Invalid characters detected in the model name, the new model name will be: " + "\033[1m" + sanitize_input(model_name) + "\033[0m")
                if not prompt_statement("Proceed ? (answering NO will return you to the model name selection process)"):
                    model_name, is_new_release = select_model_package_and_name(sanitize_input(model_name), previous_model_tag_data, predicted_version, stat_comparison_data)
                else:
                    model_name = sanitize_input(model_name)
                    model_prefix = model_name

            if previous_model_tag_data is not False and model_name == previous_model_tag_data.model_name:
                tmp_model_name = "\033[1m" + model_prefix + "-" + previous_model_tag_data.version + "\033[0m"
                predicted_tmp_model_name = "\033[1m" + model_prefix + "-" + predicted_version + "\033[0m"
                tmp_specialist_model_name = "\033[1m" + model_prefix + "-" + previous_model_tag_data.version + "\033[0m"

                if different_organisation_base:
                    is_new_release = True
                    tmp_model_name = "\033[1m" + model_prefix + "-1.0" + "\033[0m"
                    if prompt_statement("\n Do you want to update the tag number of an existing model ? (improvement of model)  e.g : " + previous_model_name + " -> " + tmp_model_name) is False:
                        if prompt_statement("Do you want to create a specialist model tag? e.g : " + tmp_specialist_model_name + " -> " + "\033[1m" + "<new_model_name>-1.0" + "\033[0m"):
                            model_name = input("Give the model tag a name, the version will be 1.0 by default:")
                            is_new_release = True
                            break
                    else:
                        break
                elif prompt_statement("\n Do you want to update the tag number of an existing model ? (improvement of model)  e.g : " + tmp_model_name + " -> " + predicted_tmp_model_name) is False:
                    if prompt_statement("Do you want to create a specialist model tag? e.g : " + tmp_specialist_model_name + " -> " + "\033[1m" + "<new_model_name>-1.0" + "\033[0m"):
                        model_name = input("Give the model tag a name, the version will be 1.0 by default:")
                        is_new_release = True
                        break
                else:
                    break

            elif previous_model_tag_data is not False and model_name != previous_model_tag_data.model_name:
                predicted_new_model_name = "\033[1m" + model_prefix + "-1.0" + "\033[0m" 

                if prompt_statement("Do you want to create a specialist model tag? e.g : " + previous_model_name + " -> "+ predicted_new_model_name):
                    print("Using "  + "\033[1m" + model_name  + "\033[0m" + " as the new model name.")
                else:
                    model_name = input("Give the model tag a name, the version will be 1.0 by default:")
                is_new_release = True
            else:
                is_new_release = True
                if prompt_statement("This is a new model release (version will be set to 1.0 by default), are you satisified with the name ? given name : " + "\033[1m" + model_prefix + "\033[0m" + " . The tag will be :" + "\033[1m" + model_prefix + "-1.0" + "\033[0m"):
                    print("Using "  + "\033[1m" + model_name  + "\033[0m" + " as the new model name.") 
                else:
                    model_name = input("Give the model tag a name, the version will be 1.0 by default:")
                break

        return model_name, is_new_release

    logging.error("No model name has been provided, and the models detected in the current folder have no model tag data history...")
    logging.error("Please re-run the command and provide a name for the model as a parameter: python3 -m medcat package [model_name]")
    logging.error("Exiting...")
    sys.exit()


def inject_tag_data_to_model_files(model_folder_path, model_name, parent_model_name, version, commit_hash, git_repo_url, parent_model_tag, changed_files):
    organisation_name = get_auth_environment_vars()["git_organisation_name"]
    model_tag_data = ModelTagData(organisation_name=organisation_name, model_name=model_name, parent_model_name=parent_model_name,
                                                                       version=version,
                                                                        commit_hash=commit_hash, git_repo=git_repo_url, parent_model_tag=parent_model_tag,
                                                                         medcat_version=get_medcat_package_version())

    # remove the ".dvc" extension if any files contain it
    changed_files = [changed_file_name[:-4] if changed_file_name.endswith(".dvc") else changed_file_name for changed_file_name in changed_files]

    for file_name in get_permitted_push_file_list(): 
        file_path = os.path.join(model_folder_path, file_name)

        if os.path.isfile(file_path) and file_name in changed_files:
            logging.info("Updating model object : " + organisation_name + "-" + model_name + "-" + str(version) + ", " + file_name + " with tag data...")

            loaded_model_file = load_file_from_model_storage(model_folder_path=model_folder_path, file_name=file_name, bypass_model_path=True)

            if file_name.endswith(get_model_binary_file_extension()):
                loaded_model_file.vc_model_tag_data = model_tag_data

                if any(m in file_path for m in ["vocab.dat", "cdb.dat"]):
                    loaded_model_file.save(path=file_path)
                else:
                    with open(file_path, 'wb') as f:
                        dill.dump(loaded_model_file, f)

            if file_name == "MedCAT_Export.json":
                with open(file_path, "w+", encoding="utf8") as f:
                    loaded_model_file["vc_model_tag_data"] = asdict(model_tag_data)
                    f.write(json.dumps(loaded_model_file))

            logging.info("Saving of : " + file_name + " is complete...")


def detect_model_name_from_files(model_folder_path="./"):
    model_data = {}

    for file_name in get_permitted_versioned_files(): 
        if os.path.isfile(os.path.join(model_folder_path, file_name)):
            loaded_model_file = load_file_from_model_storage(model_folder_path=model_folder_path, file_name=file_name, bypass_model_path=True, ignore_non_model_files=False)

            if loaded_model_file is not False:
                model_data[file_name] = {}

                # used for stat comparison for certain files
                model_data[file_name]["full_file_data"] = loaded_model_file

                if type(loaded_model_file) is not dict or hasattr(loaded_model_file, "vc_model_tag_data"):
                    model_data[file_name]["vc_model_tag_data"] = loaded_model_file.vc_model_tag_data
                elif "vc_model_tag_data" in loaded_model_file.keys():
                    model_data[file_name]["vc_model_tag_data"] = ModelTagData(*loaded_model_file["vc_model_tag_data"].values())

                if type(loaded_model_file) is dict and "trainer_stats" in loaded_model_file.keys():
                    model_data[file_name]["trainer_stats"] = loaded_model_file["trainer_stats"]

                if hasattr(loaded_model_file, "cdb_stats"):
                    model_data[file_name]["cdb_stats"] = asdict(loaded_model_file.cdb_stats)

    return False if len(model_data) == 0 else model_data


def compare_model_stats(current_model_file_data, old_model_file_data):

    stat_differences = {}

    for file_name in get_permitted_versioned_files():
        stat_differences[file_name]  = {}
        if file_name in current_model_file_data.keys() and file_name in old_model_file_data.keys():
            current_data = current_model_file_data[file_name]["full_file_data"] 
            old_data = old_model_file_data[file_name]["full_file_data"] 

            if get_model_binary_file_extension() in file_name:
                old_model_tag_name = current_data.vc_model_tag_data.model_name + "-" + old_data.vc_model_tag_data.version
            else:
                old_model_tag_name = current_data["vc_model_tag_data"]["model_name"] + "-" + old_data["vc_model_tag_data"]["version"]

            stat_differences[file_name] = {"current": {}, old_model_tag_name: {}}

            if "cdb" in file_name:
                current_data_vals = list(itertools.chain(*list(current_data.name2cuis.values())))
                old_data_vals = list(itertools.chain(*list(old_data.name2cuis.values())))
                stat_differences[file_name]["current"].update({"cui_names": set(current_data.name2cuis.keys()) - set(old_data.name2cuis.keys())})
                stat_differences[file_name][old_model_tag_name].update({"cui_names": set(old_data.name2cuis.keys()) - set(current_data.name2cuis.keys())})
                stat_differences[file_name]["current"].update({"cui_ids": set(current_data_vals) - set(old_data_vals)})
                stat_differences[file_name][old_model_tag_name].update({"cui_ids": set(old_data_vals) - set(current_data_vals)})
            elif "vocab" in file_name:
                stat_differences[file_name]["current"].update({"index2word": set(current_data.index2word.items()) - set(old_data.index2word.items())})
                stat_differences[file_name][old_model_tag_name].update({"index2word": set(old_data.index2word.items()) - set(current_data.index2word.items())})
            elif "MedCAT_Export" in file_name:
                for stat, value in current_data["trainer_stats"].items():
                    if type(value) in [int, float]:
                        stat_differences[file_name]["current"].update({stat: (value - old_data["trainer_stats"][stat])})
                        stat_differences[file_name][old_model_tag_name].update({stat: (old_data["trainer_stats"][stat] - value)})
    stat_improvement = {}

    for k,v in stat_differences["MedCAT_Export.json"]["current"].items():
        if float(v) < 0.0:
            stat_improvement = {k: False}
        elif float(v) > 0.0:
            stat_improvement = {k: True}
        else:
            stat_improvement = {k: "NONE"}

    new_release = {}

    for k,v in stat_differences["cdb.dat"]["current"].items():
        if len(v) > 0:
            new_release = {k: True}
        else: 
            new_release = {k: False}

    is_actual_new_release = len([v is False for v in list(new_release.values())]) > len([v is True for v in list(new_release.values())])
    is_stat_improvement = "NONE" if len([v == "NONE" for v in list(stat_improvement.values())]) == len(stat_improvement.keys()) \
                          else len([v is False for v in list(stat_improvement.values())]) < len([v is True for v in list(stat_improvement.values())])


    return stat_differences, stat_improvement, new_release, is_actual_new_release, is_stat_improvement


def upload_model(model_name, version):

    failed_after_tag_creation = False

    release_id = None

    parent_model_name = ""
    upload_headers = get_git_download_headers()
    headers = get_git_default_headers()

    git_repo_url = get_auth_environment_vars()["git_repo_url"]

    # folder where we are now (where we called the package command)
    current_folder = os.getcwd()

    # get information about the model files we currently want to package
    model_file_data = detect_model_name_from_files(current_folder)

    # get model file data that is most recent (higher version) from all the files
    previous_tag_model_data = False

    if model_file_data is not False:
        biggest_version = ()

        for file_name in model_file_data.keys():
            logging.debug("file_name : " + file_name, " | tag_data : ", model_file_data[file_name]["vc_model_tag_data"])

            if "vc_model_tag_data" in model_file_data[file_name].keys():
                if model_file_data[file_name]["vc_model_tag_data"].version != "":
                    current_timestamp = datetime.datetime.strptime(model_file_data[file_name]["vc_model_tag_data"].timestamp, DEFEAULT_DATETIME_FORMAT)
                    if len(biggest_version) > 0:
                        biggest_ver_timestamp = datetime.datetime.strptime(biggest_version[1], DEFEAULT_DATETIME_FORMAT)
                        if current_timestamp > biggest_ver_timestamp:
                            biggest_version = ()
                            biggest_version = (file_name, model_file_data[file_name]["vc_model_tag_data"].timestamp)
                    else:
                        biggest_version = (file_name, model_file_data[file_name]["vc_model_tag_data"].timestamp)

        if len(biggest_version) > 0:
            previous_tag_model_data = model_file_data[biggest_version[0]]["vc_model_tag_data"]

    old_model_file_data = False
    stat_comparison_data = None

    if previous_tag_model_data:
        old_model_file_data = detect_model_name_from_files(get_downloaded_local_model_folder(previous_tag_model_data.model_name + "-" + previous_tag_model_data.version))

        stat_differences, stat_improvement, new_release, is_actual_new_release, is_stat_improvement = compare_model_stats(model_file_data, old_model_file_data)
        stat_comparison_data = {"stat_differences": stat_differences, "stat_improvement": stat_improvement, "new_release": new_release, "is_actual_new_release": is_actual_new_release, "is_stat_improvement": is_stat_improvement}

    # this is the predicted version number, will change to 1.0 if its a new release
    version = generate_model_version(model_name, version, previous_tag_model_data) 

    # determine the final model name
    model_name, is_new_release = select_model_package_and_name(model_name, previous_tag_model_data, predicted_version=version, stat_comparison_data=stat_comparison_data)

    # version reset if it's a new release
    if is_new_release:
        version = "1.0"
        # we add the organisation name since this is a specialist model
        if previous_tag_model_data is False:
            tag_name = model_name + "-" + version
        elif previous_tag_model_data.organisation_name == get_auth_environment_vars()["git_organisation_name"]:
            tag_name = model_name + "-" + version
        else:
            tag_name = get_auth_environment_vars()["git_organisation_name"] + "-" + model_name + "-" + version
    else:
        tag_name = model_name + "-" + version

    # create folder for new model release
    # folder where the original model files are: $HOME/.cache/medcat/models/...
    new_model_package_folder = os.path.join(get_local_model_storage_path(), tag_name)

    if get_downloaded_local_model_folder(tag_name) is not False:
        shutil.rmtree(new_model_package_folder, ignore_errors=True)

    create_model_folder(tag_name)

    if previous_tag_model_data is not False:
        tmp_old_full_model_tag_name = previous_tag_model_data.model_name + "-" + str(previous_tag_model_data.version)
        logging.info("Creating new folder for the release... checking out from tag: " + tmp_old_full_model_tag_name)
        create_new_base_repository(new_model_package_folder, git_repo_url, checkout_full_tag_name=tmp_old_full_model_tag_name)
    else:
        create_new_base_repository(new_model_package_folder, git_repo_url)

    bundle_file_path = ""

    try:
        logging.info("Current directory:" + current_folder)
        logging.info("Current GIT working dir: " + new_model_package_folder)
        logging.info("===================================================================")
        logging.info("Git status:")
        subprocess.run(["git", "status"], cwd=new_model_package_folder)
        logging.info("===================================================================")
        logging.info("DVC status:")
        subprocess.run([sys.executable, "-m", "dvc","status"], cwd=new_model_package_folder)
        logging.info("===================================================================")

        repo = Repo(new_model_package_folder, search_parent_directories=False)

        # fetch all tags
        subprocess.run(["git", "fetch", "--tags", "--force"], cwd=new_model_package_folder)

        copy_model_files_to_folder(current_folder, new_model_package_folder)

        # check if this is now a parent:
        parent_model_tag = ""

        if previous_tag_model_data is not False:
            if model_name != previous_tag_model_data.model_name:
                parent_model_name = previous_tag_model_data.model_name
                parent_model_tag = previous_tag_model_data.model_name + "-" + previous_tag_model_data.version
            else:
                parent_model_name = previous_tag_model_data.parent_model_name

                # all new releases have parent model
                parent_model_tag = previous_tag_model_data.parent_model_tag

        release_name = tag_name     

        # attempt to generate new model_name and inject it into the model file data
        # IMPORTANT: the commit points to the parent model or the last commit of the repository (non-tag) commit
        # if there have been changes on the file hashes since the previous commit, then, we can inject the new release data into the model files
        # Update dvc repo files (if any) before checking for untracked files ( we need to regenerate dvc file hashes if there were changes)
        # We need to check the files before injecting new tag/release data into them, otherwise they will always be flagged as changed..
        subprocess.run([sys.executable, "-m", "dvc", "commit", "--force"], cwd=new_model_package_folder, text=True)

        os.chdir(new_model_package_folder)

        changed_files = [item.a_path for item in repo.index.diff(None)]
        untracked_files = repo.untracked_files

        full_changed_file_list = untracked_files + changed_files

        if full_changed_file_list:

            inject_tag_data_to_model_files(new_model_package_folder, model_name, parent_model_name=parent_model_name,
            version=version, commit_hash=str(repo.head.commit), git_repo_url=git_repo_url, parent_model_tag=parent_model_tag, changed_files=full_changed_file_list)

            for file_name in changed_files:
                subprocess.run([sys.executable, "-m", "dvc", "commit", file_name, "--force"], cwd=new_model_package_folder, text=True)

            # get  newly changed files after the update
            changed_files = [item.a_path for item in repo.index.diff(None)]

            logging.info("There are files which are untracked.")
            logging.info("Untracked files:" + str(untracked_files))
            logging.info("Unstaged files:" + str(changed_files))

            for root, dirs, files in os.walk(new_model_package_folder):
                if ".gitignore" in files:
                    repo.git.add(os.path.join(root, ".gitignore"))

            if untracked_files:
                if prompt_statement("Do you wish to add them manually or to add all ? Yes = manual, No = add all"):
                    for file_name in untracked_files:
                        if prompt_statement("Add : " + file_name + " to the DVC repo ?"):      
                            if ".dvc" not in file_name and file_name not in repo.ignored(file_name):
                                subprocess.run([sys.executable, "-m", "dvc","add", file_name], cwd=new_model_package_folder)
                                repo.git.add(file_name + ".dvc")
                            elif ".dvc" in file_name and file_name not in repo.ignored(file_name):
                                repo.git.add(file_name)
                            else:
                                logging.info("Cannot add file, it is either a file ignored in .gitignore or a DVC handled file.")
                else: 
                    for file_name in untracked_files:
                        if ".dvc" not in file_name and file_name not in repo.ignored(file_name):
                            subprocess.run([sys.executable, "-m", "dvc","add", file_name], cwd=new_model_package_folder)
                    repo.git.add(all=True)

            for file_name in changed_files:
                repo.git.add(file_name)

        staged_files = len(repo.index.diff("HEAD"))

        logging.info("Staged files:" + str(staged_files))

        if staged_files:
            if prompt_statement("Do you want to create the tag: " + tag_name + " and release " + release_name + "?"):

                repo.index.commit(tag_name)

                new_tag = repo.create_tag(path=tag_name, ref=repo.head.commit.hexsha)

                repo.remotes.origin.push(new_tag)

                failed_after_tag_creation = True

                model_card_info_string, model_card_info_json = generate_model_card_info(git_repo_url, 
                                                                                        model_name, 
                                                                                        parent_model_name, 
                                                                                        new_model_package_folder, 
                                                                                        version, 
                                                                                        tag_name, 
                                                                                        parent_model_tag, model_file_data=model_file_data)

                tag_data = {
                    "tag_name": tag_name,
                    "name": release_name,
                    "draft": False,
                    "prerelease": False,
                    "target_commitish": repo.head.commit.hexsha,
                    "body": model_card_info_string
                }

                create_tag_request = requests.post(url=get_git_api_request_url() + "releases", data=json.dumps(tag_data), headers=headers)

                if create_tag_request.status_code == 201:
                    logging.info("Success, created release : " + release_name + ", with tag : " + tag_name + " .")
                    subprocess.run(["git", "bundle", "create", str(tag_name) + ".bundle", "--all"], cwd=new_model_package_folder)

                    bundle_file_path = new_model_package_folder + "/" + str(tag_name) + ".bundle"
                    req_release_data = requests.get(url=get_git_api_request_url() + "releases/tags/" + str(tag_name), headers=headers)

                    release_id = str(req_release_data.json()["id"])

                    if req_release_data.status_code == 200:
                        release_id = str(req_release_data.json()["id"])
                        file_asset_url = get_git_api_upload_url() + "releases/" + release_id + "/assets?name=" + str(tag_name) + ".bundle"
                        delete_asset_url = get_git_api_request_url() + "releases/assets/"

                        for asset in req_release_data.json()["assets"]:
                            req_delete_release_asset = requests.delete(url=delete_asset_url + str(asset["id"]), headers=headers)
                            if req_delete_release_asset.status_code >= 400:
                                logging.info("Response: " + str(req_delete_release_asset.status_code)  + " Failed to delete asset: " + str(asset["name"]) + "  id: ", str(asset["id"]))
                                logging.info("Reason:" + req_delete_release_asset.text)

                        with open(bundle_file_path, "rb") as file:
                            data = file.read()
                            req_upload_release_asset = requests.post(url=file_asset_url, data=data, headers=upload_headers)

                            if req_upload_release_asset.status_code == 201:
                                logging.info("Asset : " + file_asset_url + " uploaded successfully")
                            else:
                                logging.info("Response: " + str(req_upload_release_asset.status_code) + " Failed to upload asset: " + bundle_file_path)
                                logging.info("Reason:" + req_upload_release_asset.text)

                        modelcard_json_url = get_git_api_upload_url() + "releases/" + release_id + "/assets?name=modelcard.json"
                        req_modelcard_json_upload_asset = requests.post(url=modelcard_json_url, data=model_card_info_json, headers=upload_headers)

                        if req_modelcard_json_upload_asset.status_code == 201:
                            logging.info("Asset : " + modelcard_json_url + " uploaded successfully")
                        else:
                            logging.info("Response: " + str(req_modelcard_json_upload_asset.status_code) + " Failed to upload asset: " + modelcard_json_url)
                            logging.info("Reason:" + req_modelcard_json_upload_asset.text)

                elif create_tag_request.status_code == 200:
                    logging.info("Success, created release : " + release_name + ", with tag : " + tag_name + " .")
                else:
                    if "already exists" in create_tag_request.text:
                        logging.error("TAG ALREADY EXISTS")
                    if release_id is None:
                        req_release_data = requests.get(url=get_git_api_request_url() + "releases/tags/" + str(tag_name), headers=headers)
                        release_id = str(req_release_data.json()["id"])
                    raise Exception("Failed to create release : " + release_name + ", with tag : " + tag_name + " . \n" + "Reason:" + create_tag_request.text)

                subprocess.call([sys.executable, "-m", "dvc", "push"], cwd=new_model_package_folder)
                logging.info("Model pushed successfully!")
            else:
                raise Exception("Could not push new model version")

        else:
            logging.info("No changes to be submitted. Checking model files with storage server for potential update pushing....")
            subprocess.run([sys.executable, "-m", "dvc", "push"], cwd=new_model_package_folder)

        return True

    except Exception as exception:
        """
            Resets the head commit to default previous one, it should happen in case of any error.
            If the tag has been already pushed then it will be deleted from the git repo.
        """
        logging.error("description: " + repr(exception))
        traceback.print_exc()
        logging.warning("Push process cancelled... reverting state...")

        if tag_name != "" and failed_after_tag_creation: 
            logging.warning("Deleting tag " + tag_name + " because the push operation has failed and changes were reverted.")
            subprocess.run(["git", "tag", "--delete", tag_name], cwd=new_model_package_folder)
            subprocess.run(["git", "push", "--delete", tag_name], cwd=new_model_package_folder)

    finally:
        if bundle_file_path != "":
            os.remove(bundle_file_path)

    return False


def generate_model_card_info(git_repo_url, model_name, parent_model_name, model_folder_path, version="", tag_name="", parent_model_tag_name="", model_file_data=None):
    """
       input:
       -----
           - model card information

       output:
       ------
          - model_card string (to be used to display github releases page
          - model_card_json object (json format for model card to be uploaded as asset to associated model release
    """

    model_card_json = {}
    model_card = ""

    authors = []

    if model_file_data is not None:
        for file, data in model_file_data.items():
            if "trainer_stats" in data.keys():
                model_card_json.update({"trainer_stats": {k: str(v) if type(v) is not list else "".join(map(str, v)) for k,v in data["trainer_stats"].items()}})
                authors.extend(data["trainer_stats"]["authors"])
            if "cdb_stats" in data.keys():
                model_card_json.update({"cdb_stats": {k: str(v) for k,v in data["cdb_stats"].items()}})

    model_card_json["model_name"] = model_name
    model_card_json["tag_name"] = tag_name
    model_card_json["version"] = version
    model_card_json["model_authors"] = "".join(map(str,set(authors)))
    model_card_json["medcat_version"] = get_medcat_package_version()

    if parent_model_name == "":
        model_card_json['parent_model_name'] = "N/A"
        model_card_json['parent_model_tag_url'] = "N/A"
    else:
        model_card_json['parent_model_name'] = parent_model_name
        model_card_json['parent_model_tag_url'] = git_repo_url[:-4] + "/releases/tag/" + parent_model_tag_name if str(git_repo_url).endswith(".git") else ""
        model_card_json['parent_model_tag_url'] = "<a href="+ model_card_json['parent_model_tag_url']  + ">" + parent_model_tag_name + "</a>"

    model_card_path = os.path.join(model_folder_path, "modelcard.md")

    if os.path.isfile(model_card_path):
        with open(model_card_path) as f:
            model_card = f.read()

        model_card = model_card.replace("<model_name>-<parent_model_name>-<model_version>", model_card_json['tag_name'])
        model_card = model_card.replace("<model_name>", model_card_json['model_name'])
        model_card = model_card.replace("<parent_model_name>", model_card_json['parent_model_name'])
        model_card = model_card.replace("<parent_model_tag>", model_card_json['parent_model_tag_url'])
        model_card = model_card.replace("<model_version>", model_card_json['version'])
        model_card = model_card.replace("<medcat_version>", model_card_json['medcat_version'])
        model_card = model_card.replace("<model_author>", model_card_json["model_authors"])

        if "cdb_stats" in model_card_json.keys():
            model_card = model_card.replace("<ontology_type>", model_card_json["cdb_stats"]["ontology_type"])
            model_card = model_card.replace("<ontology_version>", model_card_json["cdb_stats"]["ontology_version"])

        if "trainer_stats" in model_card_json.keys():
            model_card = model_card.replace("<f1_score>", model_card_json["trainer_stats"]["concept_f1"])
            model_card = model_card.replace("<precision_score>", model_card_json["trainer_stats"]["concept_precision"])
            model_card = model_card.replace("<recall_score>", model_card_json["trainer_stats"]["concept_recall"])

            # replace other tags | <meta_model_name> | <meta_model_tag> with empty since they arent used here, they are just placeholders
            model_card = model_card.replace("<meta_model_tag> |", "")    
            model_card = model_card.replace("<meta_tasks> |", "")    

            meta_projects_data_md = ""

            meta_project_data = data["trainer_stats"]["meta_project_data"]

            proj_count = 0

            if len(meta_project_data) > 0:
                for meta_project_name, meta_project_tasks in meta_project_data.items():
                    proj_count += 1
                    meta_projects_data_md += meta_project_name + " | " + str(meta_project_tasks) + " | -  \n"

                    if proj_count != len(meta_project_data.keys()):
                        meta_projects_data_md += "|"

            model_card = model_card.replace("<meta_model_name>", meta_projects_data_md)

        if "metacat_stats" in model_card_json.keys():
            model_card = model_card.replace("<meta_model_precision_score>", model_card_json["metacat_stats"]["precision"])
            model_card = model_card.replace("<meta_model_f1_score>", model_card_json["metacat_stats"]["f1"])
            model_card = model_card.replace("<meta_model_recall_score>", model_card_json["metacat_stats"]["recall"])
            model_card = model_card.replace("<meta_model_nepochs>", model_card_json["metacat_stats"]["nepochs"])
            model_card = model_card.replace("<meta_model_learning_rate>", model_card_json["metacat_stats"]["learning_rate"])
            model_card = model_card.replace("<meta_model_score_average>", model_card_json["metacat_stats"]["score_average"])
    else:
        logging.error("Could not find model card file that holds a brief summary of the model data & specs.")

    return model_card, json.dumps(model_card_json)


def generate_model_version(model_name, version, previous_model_tag_data=False):
    """
        Generates the consecutive version number of a model according to its current tag and previous tag
    """
    try:
        # if the model has a history, and the provided model name is empty then we assume its an update/improvement of the same model

        if version == "auto" and previous_model_tag_data is not False and (model_name == previous_model_tag_data.model_name or model_name == ""):
            version = '.'.join(map(str, str(int(''.join(map(str, str(previous_model_tag_data.version).split('.')))) + 1)))

        # if its still auto then it means it is a new release
        if version == "auto":
            version = "1.0"

    except Exception as exception:
        version = "1.0"
        logging.error("Error when generating model tag/release name: " + repr(exception))

    return version


def package(full_model_tag_name="", version="auto"):
    return upload_model(full_model_tag_name, version=version)


if __name__ == '__main__':
    fire.Fire(package)
