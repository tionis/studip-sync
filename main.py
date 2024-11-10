#!/bin/env python3
import argparse
import os
import configparser
import platform
import json
import shutil
import subprocess
import requests
import tempfile
import re
import sys

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

class FzfPrompt:
    def __init__(self, executable_path=None):
        if executable_path:
            self.executable_path = executable_path
        elif not shutil.which("fzf") and not executable_path:
            raise SystemError(
                f"Cannot find 'fzf' installed on PATH.")
        else:
            self.executable_path = "fzf"

    def prompt(self, choices=None, fzf_options="", delimiter='\n'):
        # convert a list to a string [ 1, 2, 3 ] => "1\n2\n3"
        choices_str = delimiter.join(map(str, choices))
        selection = []

        with tempfile.NamedTemporaryFile(delete=False) as input_file:
            with tempfile.NamedTemporaryFile(delete=False) as output_file:
                # Create a temp file with list entries as lines
                input_file.write(choices_str.encode('utf-8'))
                input_file.flush()

        # Invoke fzf externally and write to output file
        os.system(
            f"{self.executable_path} {fzf_options} < \"{input_file.name}\" > \"{output_file.name}\"")

        # get selected options
        with open(output_file.name, encoding="utf-8") as f:
            for line in f:
                selection.append(line.strip('\n'))

        os.unlink(input_file.name)
        os.unlink(output_file.name)

        return selection

class StudipSync:
    # Config
    data_path = "."
    studip_host = "studip.uni-passau.de"
    prefix = "/studip/api.php"
    auth_method = "cookie"
    browser = "firefox"
    use_git = False
    git_commit_message_prefix = "studip-sync: "

    # Cache
    cookie = None
    current_semester = None
    user_id = None

    # constructor defaults all non specified config values to the default values (config is passed through from arguments in some explicit way
    def __init__(self, config={}):
        for key in config:
            if hasattr(self, key):
                setattr(self, key, config[key])
        if self.use_git:
            if not shutil.which("git"):
                raise FileNotFoundError("git not found")
            git_top_level_process = subprocess.run(["git", "-C", self.data_path, "rev-parse", "--show-toplevel"], capture_output=True)
            if git_top_level_process.returncode != 0:
                eprint("No git repository found in data path")
                eprint("Please initialize the repository with 'git init'")
                eprint("The author also recommends using lfs to track large files")
                raise Exception("No git repository found")
            self.top_level = git_top_level_process.stdout.decode("utf-8").strip()
        self.load_current_semester()

    def get_firefox_profile_dir(self): # Get main firefox profile directory
        if platform.system() == "Windows":
            firefox_dir = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Mozilla", "Firefox", "Profiles")
        elif platform.system() == "Linux":
            firefox_dir = os.path.join(os.path.expanduser("~"), ".mozilla", "firefox")
        elif platform.system() == "Darwin":
            firefox_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Firefox", "Profiles")
        else:
            raise NotImplementedError(f"Platform \"{platform.system()}\" not supported")
        # Reading profiles.ini to get the default profile
        profiles_ini = os.path.join(firefox_dir, "profiles.ini")
        firefoxProfilesConfig = configparser.ConfigParser()
        firefoxProfilesConfig.read(profiles_ini)
        profile = firefoxProfilesConfig["Profile0"]["Path"] # Unsure if this is correct
        return os.path.join(firefox_dir, profile)

    def get_cookie_from_browser(self):
        if self.cookie is not None:
            return self.cookie
        if self.browser == "firefox":
            cookieFilePath = os.path.join(self.get_firefox_profile_dir(), "sessionstore-backups", "recovery.jsonlz4")
            # Check if dejsonlz4 is in PATH
            dejsonlz4PathLoc = shutil.which("dejsonlz4")
            if dejsonlz4PathLoc:
                dejsonlz4 = dejsonlz4PathLoc
            else:
                if shutil.which("dejsonlz4.com"):
                    dejsonlz4 = "dejsonlz4.com"
                else:
                    raise FileNotFoundError("dejsonlz4 not found")
            studip_host = self.studip_host
            cookies_process = subprocess.run([dejsonlz4, cookieFilePath], capture_output=True)
            cookies = json.loads(cookies_process.stdout)["cookies"]
            for cookie in cookies:
                if cookie["host"] == studip_host and cookie["name"] == "Seminar_Session":
                    self.cookie = cookie['value']
                    return cookie['value']
            raise KeyError("Cookie not found")
        else:
            raise NotImplementedError(f"Browser \"{self.browser}\" not supported")

    def get_cookie(self):
        if self.auth_method == "cookie":
            return self.get_cookie_from_browser()
        else:
            raise NotImplementedError(f"Auth method \"{self.auth_method}\" not supported")

    def get_user_id(self):
        if self.user_id is not None:
            return self.user_id
        self.user_id = self.get("/user")["user_id"]
        return self.user_id

    def get_req(self, path):
        path = path.removeprefix(self.prefix)
        url = f"https://{self.studip_host}{self.prefix}{path}"
        eprint(f"GET {url}")
        resp = requests.get(url, headers={"Cookie": f"Seminar_Session={self.get_cookie()}"})
        if resp.status_code != 200:
            raise Exception(f"Failed to get {url}: {resp.status_code}")
        return resp

    def get_no_parse(self, path):
        return self.get_req(path).content

    def get(self, path):
        return self.get_req(path).json()

    def get_subfolders(self, folder):
        if "is_readable" in folder and folder["is_readable"]:
            subfolders = self.get(f"/folder/{folder['id']}/subfolders")["collection"]
            folder["files"] = self.get(f"/folder/{folder['id']}/files")["collection"]
            folder["subfolders"] = []
            for subfolder in subfolders:
                folder["subfolders"].append(self.get_subfolders(subfolder))
            return folder
        else:
            return {"files": [], "subfolders": []}

    def get_courses(self, semester_id=None):
        courses = []
        if semester_id is None:
            raw_courses = self.get(f"/user/{self.get_user_id()}/courses")["collection"]
        else:
            raw_courses = self.get(f"/user/{self.get_user_id()}/courses?semester={semester_id}")["collection"]

        for course in raw_courses.values():
            path = course["modules"]["documents"] if "documents" in course["modules"] else None
            if path is not None:
                course["top_folder"] = self.get(path)

            if "top_folder" in course and "id" in course["top_folder"] and course["top_folder"]["id"]:
                course["top_folder"]["files"] = self.get(f"/folder/{course['top_folder']['id']}/files")["collection"]

            if "top_folder" in course and "subfolders" in course["top_folder"] and course["top_folder"]["subfolders"]:
                subfolders = []
                for subfolder in course["top_folder"]["subfolders"]:
                    subfolders.append(self.get_subfolders(subfolder))
                course["top_folder"]["subfolders"] = subfolders

            courses.append(course)

        return courses

    
    def escape_filename(self, name):
        return name.replace("/", "_")

    def clean_path(self, path):
        # forbidden symbols: "<" ">" ":" "\"" "\\" "|" "?" "*"
        dirty_symbols_regex = r"[<>:\"\\|?*]"
        return re.sub(dirty_symbols_regex, "_", path)

    def get_current_semester(self):
        if self.current_semester is None:
            return self.load_current_semester()
        return self.current_semester

    def update_links(self):
        # Update symlinks
        current_semester_path = os.path.join(self.data_path, "this-semester")
        if os.path.exists(current_semester_path):
            eprint(f"Removing old this-semester directory at {current_semester_path}")
            shutil.rmtree(current_semester_path)
        os.mkdir(current_semester_path)
        courses = self.get_courses(self.get_current_semester())
        for course in list(set([self.escape_filename(course["title"]) for course in courses])):
            os.symlink(os.path.join("..", "archive" , course), os.path.join(current_semester_path, course), target_is_directory=True)
        if self.use_git:
            # Count changes in this-semester dir
            changesProcess = subprocess.run(["git", "-C", self.data_path, "diff", "--name-only", "--", "current-semester"], capture_output=True)
            changes = changesProcess.stdout.decode("utf-8").split("\n")
            if len(changes) > 0:
                # Commit changes
                subprocess.run(["git", "-C", self.data_path, "add", current_semester_path])
                subprocess.run(["git", "-C", self.data_path, "commit", "-m", self.git_commit_message_prefix + "updated this-semester links"])
                subprocess.run(["git", "-C", self.data_path, "push"])

    def select_semester(self, semester=None):
        semesters = self.get("/semesters")["collection"]
        semesterNameToMeta = {}
        for val in semesters.values():
            semesterNameToMeta[val["title"]] = val
        if semester is None:
            fzf = FzfPrompt()
            chosen = fzf.prompt(semesterNameToMeta.keys())
            if len(chosen) == 0:
                raise Exception("No semester chosen")
            semester = chosen[0]
        semester_id = semesterNameToMeta[semester]["id"]        
        self.current_semester = semester_id
        self.save_current_semester(semester_id)
        self.update_links()

    def load_current_semester(self):
        if os.path.exists(os.path.join(self.data_path, ".current-semester")):
            with open(os.path.join(self.data_path, ".current-semester"), "r") as f:
                self.current_semester = f.read().strip()
        return self.current_semester

    def save_current_semester(self, semester_id):
        # Ensure that the directory exists
        os.makedirs(self.data_path, exist_ok=True)
        with open(os.path.join(self.data_path, ".current-semester"), "w") as f:
            f.write(semester_id)
        if self.use_git:
            subprocess.run(["git", "-C", self.data_path, "add", ".current-semester"])
            subprocess.run(["git", "-C", self.data_path, "commit", "-m", self.git_commit_message_prefix + "updated current semester"])
            subprocess.run(["git", "-C", self.data_path, "push"]) # IDEA: push will be done one layer above this if head changed


    def get_files(self, folder, parent_path):
        files = {}
        for file in folder["files"]:
            files[f"{parent_path}/{file['name']}"] = file["id"]
        if "subfolders" in folder:
            for subfolder in folder["subfolders"]:
                for key,value in self.get_files(subfolder, f"{parent_path}/{folder['name']}").items():
                    files[key] = value
        return files

    def sync(self):
        courses = self.get_courses(self.current_semester)
        files = {}
        for course in courses:
            if "top_folder" in course:
                for key, value in self.get_files(course["top_folder"], course["title"]).items():
                    files[key] = value

        for file in files:
            file_path = self.clean_path(os.path.join(self.data_path, "archive", file))
            if not os.path.exists(file_path):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                eprint(f"Downloading {file} to {file_path}")
                file_meta = self.get(f"/file/{files[file]}")
                if file_meta["is_downloadable"]:
                    with open(file_path, 'wb') as f:
                        f.write(self.get_no_parse(f"/file/{files[file]}/download"))
                else:
                    eprint(f"File {file} is not downloadable")
                    eprint(f"Creating placeholder file {file_path}")
                    # Write studip-sync:non-downloadable-file into placeholder file
                    with open(file_path, 'w') as f:
                        f.write("studip-sync:non-downloadable-file")

        if self.use_git:
            # Count changes in archive dir
            changesProcess = subprocess.run(["git", "-C", self.data_path, "diff" , "--name-only", "--", "archive"], capture_output=True)
            changes = changesProcess.stdout.decode("utf-8").split("\n")
            if len(changes) > 0:
                # Commit changes
                subprocess.run(["git", "-C", self.data_path, "add", os.path.join(self.data_path, "archive")])
                subprocess.run(["git", "-C", self.data_path, "commit", "-m", self.git_commit_message_prefix + "updated archive"])
                subprocess.run(["git", "-C", self.data_path, "push"])

def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--auth-method', help='Authentication method', default='cookie')
    parser.add_argument('--browser', help='Browser to use for cookie extraction', default='firefox')
    parser.add_argument('--data-path', "-d",help='Path to data directory', default='.')
    parser.add_argument('--use-git', help='Use git for version control', action='store_true')
    parser.add_argument('--git-commit-message', help='Commit message for git', default='Update files')
    
    subparsers = parser.add_subparsers(dest='command')
    
    # sync subcommand
    subparsers.add_parser('sync')
    
    # select-semester subcommand
    select_parser = subparsers.add_parser('select-semester')
    select_parser.add_argument('semester', nargs='?')
    
    # get-cookie subcommand
    subparsers.add_parser('get-cookie')
    
    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()

    studip_sync = StudipSync(vars(args))
    
    if args.command == 'sync':
        studip_sync.sync()
    elif args.command == 'select-semester':
        studip_sync.select_semester(args.semester)
    elif args.command == 'get-cookie':
        print(studip_sync.get_cookie())
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
