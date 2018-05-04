# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import os
import zipfile
from azure.cli.core.commands.client_factory import get_mgmt_service_client
from azure.mgmt.resource.resources.models import ResourceGroup
from ._constants import (
    NETCORE_VERSION_DEFAULT,
    NETCORE_VERSIONS,
    NODE_VERSION_DEFAULT,
    NODE_VERSIONS,
    NETCORE_RUNTIME_NAME,
    NODE_RUNTIME_NAME,
    DOTNET_RUNTIME_NAME,
    DOTNET_VERSION_DEFAULT,
    DOTNET_VERSIONS,
    JAVA_RUNTIME_NAME,
    STATIC_RUNTIME_NAME)


def _resource_client_factory(cli_ctx, **_):
    from azure.cli.core.profiles import ResourceType
    return get_mgmt_service_client(cli_ctx, ResourceType.MGMT_RESOURCE_RESOURCES)


def web_client_factory(cli_ctx, **_):
    from azure.mgmt.web import WebSiteManagementClient
    return get_mgmt_service_client(cli_ctx, WebSiteManagementClient)


def zip_contents_from_dir(dirPath, lang):
    relroot = os.path.abspath(os.path.join(dirPath, os.pardir))
    path_and_file = os.path.splitdrive(dirPath)[1]
    file_val = os.path.split(path_and_file)[1]
    zip_file_path = relroot + "\\" + file_val + ".zip"
    abs_src = os.path.abspath(dirPath)
    with zipfile.ZipFile("{}".format(zip_file_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for dirname, subdirs, files in os.walk(dirPath):
            # skip node_modules folder for Node apps,
            # since zip_deployment will perfom the build operation
            if lang.lower() == NODE_RUNTIME_NAME and 'node_modules' in subdirs:
                subdirs.remove('node_modules')
            elif lang.lower() == NETCORE_RUNTIME_NAME:
                if 'bin' in subdirs:
                    subdirs.remove('bin')
                elif 'obj' in subdirs:
                    subdirs.remove('obj')
            for filename in files:
                absname = os.path.abspath(os.path.join(dirname, filename))
                arcname = absname[len(abs_src) + 1:]
                zf.write(absname, arcname)
    return zip_file_path


def get_runtime_version_details(file_path, lang_name):
    version_detected = None
    version_to_create = None
    if lang_name.lower() == NETCORE_RUNTIME_NAME:
        # method returns list in DESC, pick the first
        version_detected = parse_netcore_version(file_path)[0]
        version_to_create = detect_netcore_version_tocreate(version_detected)
    elif lang_name.lower() == DOTNET_RUNTIME_NAME:
        # method returns list in DESC, pick the first
        version_detected = parse_dotnet_version(file_path)
        version_to_create = detect_dotnet_version_tocreate(version_detected)
    elif lang_name.lower() == NODE_RUNTIME_NAME:
        version_detected = parse_node_version(file_path)[0]
        version_to_create = detect_node_version_tocreate(version_detected)
    elif lang_name.lower() == STATIC_RUNTIME_NAME:
        version_detected = "-"
        version_to_create = "-"
    return {'detected': version_detected, 'to_create': version_to_create}


def create_resource_group(cmd, rg_name, location):
    rcf = _resource_client_factory(cmd.cli_ctx)
    rg_params = ResourceGroup(location=location)
    return rcf.resource_groups.create_or_update(rg_name, rg_params)


def check_resource_group_exists(cmd, rg_name):
    rcf = _resource_client_factory(cmd.cli_ctx)
    return rcf.resource_groups.check_existence(rg_name)


def check_resource_group_supports_os(cmd, rg_name, location, is_linux):
    # get all appservice plans from RG
    client = web_client_factory(cmd.cli_ctx)
    plans = list(client.app_service_plans.list_by_resource_group(rg_name))
    for item in plans:
        # for Linux if an app with reserved==False exists, ASP doesn't support Linux
        if is_linux and item.location == location and not item.reserved:
            return False
        elif not is_linux and item.location == location and item.reserved:
            return False
    return True


def check_if_asp_exists(cmd, rg_name, asp_name):
    # get all appservice plans from RG
    client = web_client_factory(cmd.cli_ctx)
    for item in list(client.app_service_plans.list_by_resource_group(rg_name)):
        if item.name == asp_name:
            return True
    return False


def check_app_exists(cmd, rg_name, app_name):
    client = web_client_factory(cmd.cli_ctx)
    for item in list(client.web_apps.list_by_resource_group(rg_name)):
        if item.name == app_name:
            return True
    return False


def get_lang_from_content(src_path):
    import glob
    # NODE: package.json should exist in the application root dir
    # NETCORE & DOTNET: *.csproj should exist in the application dir
    # NETCORE: <TargetFramework>netcoreapp2.0</TargetFramework>
    # DOTNET: <TargetFrameworkVersion>v4.5.2</TargetFrameworkVersion>
    runtime_details_dict = dict.fromkeys(['language', 'file_loc', 'default_sku'])
    package_json_file = os.path.join(src_path, 'package.json')
    package_netlang_glob = glob.glob("**/*.csproj", recursive=True)
    runtime_java_file = glob.glob("**/*.war", recursive=True)
    static_html_file = glob.glob("**/*.html", recursive=True)
    if os.path.isfile(package_json_file):
        runtime_details_dict['language'] = NODE_RUNTIME_NAME
        runtime_details_dict['file_loc'] = package_json_file
        runtime_details_dict['default_sku'] = 'S1'
    elif package_netlang_glob:
        package_netcore_file = os.path.join(src_path, package_netlang_glob[0])
        runtime_lang = detect_dotnet_lang(package_netcore_file)
        runtime_details_dict['language'] = runtime_lang
        runtime_details_dict['file_loc'] = package_netcore_file
        runtime_details_dict['default_sku'] = 'F1'
    elif runtime_java_file:
        runtime_details_dict['language'] = JAVA_RUNTIME_NAME
        runtime_details_dict['file_loc'] = runtime_java_file
        runtime_details_dict['default_sku'] = 'S1'
    elif static_html_file:
        runtime_details_dict['language'] = STATIC_RUNTIME_NAME
        runtime_details_dict['file_loc'] = static_html_file[0]
        runtime_details_dict['default_sku'] = 'F1'
    return runtime_details_dict


def detect_dotnet_lang(csproj_path):
    import xml.etree.ElementTree as ET
    import re
    parsed_file = ET.parse(csproj_path)
    root = parsed_file.getroot()
    version_lang = ''
    for target_ver in root.iter('TargetFramework'):
        version_lang = re.sub(r'([^a-zA-Z\s]+?)', '', target_ver.text)
    if 'netcore' in version_lang.lower():
        return NETCORE_RUNTIME_NAME
    return DOTNET_RUNTIME_NAME


def parse_dotnet_version(file_path):
    from xml.dom import minidom
    import re
    xmldoc = minidom.parse(file_path)
    framework_ver = xmldoc.getElementsByTagName('TargetFrameworkVersion')
    version_detected = ['4.7']
    target_ver = framework_ver[0].firstChild.data
    non_decimal = re.compile(r'[^\d.]+')
    # reduce the version to '5.7.4' from '5.7'
    if target_ver is not None:
        # remove the string from the beginning of the version value
        c = non_decimal.sub('', target_ver)
        version_detected = c[:3]
    return version_detected


def parse_netcore_version(file_path):
    import xml.etree.ElementTree as ET
    import re
    version_detected = ['0.0']
    parsed_file = ET.parse(file_path)
    root = parsed_file.getroot()
    for target_ver in root.iter('TargetFramework'):
        version_detected = re.findall(r"\d+\.\d+", target_ver.text)
    # incase of multiple versions detected, return list in descending order
    version_detected = sorted(version_detected, key=float, reverse=True)
    return version_detected


def parse_node_version(file_path):
    import json
    import re
    with open(file_path) as data_file:
        data = []
        for d in find_key_in_json(json.load(data_file), 'node'):
            non_decimal = re.compile(r'[^\d.]+')
            # remove the string ~ or  > that sometimes exists in version value
            c = non_decimal.sub('', d)
            # reduce the version to '6.0' from '6.0.0'
            data.append(c[:3])
        version_detected = sorted(data, key=float, reverse=True)
    return version_detected or ['0.0']


def detect_netcore_version_tocreate(detected_ver):
    if detected_ver in NETCORE_VERSIONS:
        return detected_ver
    return NETCORE_VERSION_DEFAULT


def detect_dotnet_version_tocreate(detected_ver):
    min_ver = DOTNET_VERSIONS[0]
    if detected_ver in DOTNET_VERSIONS:
        return detected_ver
    elif detected_ver < min_ver:
        return min_ver
    return DOTNET_VERSION_DEFAULT


def detect_node_version_tocreate(detected_ver):
    if detected_ver in NODE_VERSIONS:
        return detected_ver
    # get major version & get the closest version from supported list
    major_ver = float(detected_ver.split('.')[0])
    if major_ver < 4:
        return NODE_VERSION_DEFAULT
    elif major_ver >= 4 and major_ver < 6:
        return '4.5'
    elif major_ver >= 6 and major_ver < 8:
        return '6.9'
    return NODE_VERSION_DEFAULT


def find_key_in_json(json_data, key):
    for k, v in json_data.items():
        if key in k:
            yield v
        elif isinstance(v, dict):
            for id_val in find_key_in_json(v, key):
                yield id_val
