#!/usr/bin/env python3

from  lxml import etree as ET
import argparse
import os, sys
import pandas as pd
import jinja2
import pathlib
import copy
import json

from mdedevicecontrol import Group, PolicyRule, Entry, Settings, Setting, IntuneCustomRow, Support, IntuneUXFeature, WindowsFeature, WindowsEntryType, MacEntryType
import mdedevicecontrol.convert_dc_policy as mac 

import logging
logger = logging.getLogger(__name__)

Default_Settings = Settings(
    {
        Setting.DefaultEnforcement: "Deny",
        Setting.DeviceControlEnabled: True
    }
)

def full_stack():
    import traceback, sys
    exc = sys.exc_info()[0]
    stack = traceback.extract_stack()[:-1]  # last one would be full_stack()
    if exc is not None:  # i.e. an exception is present
        del stack[-1]       # remove call of full_stack, the printed exception
                            # will contain the caught exception caller instead
    trc = 'Traceback (most recent call last):\n'
    stackstr = trc + ''.join(traceback.format_list(stack))
    if exc is not None:
         stackstr += '  ' + traceback.format_exc().lstrip(trc)
    return stackstr


def clean_up_name(name, new_space = "-"):

    clean_name = name
    clean_name = str(clean_name).lstrip()
    clean_name = str(clean_name).rstrip()
    clean_name = str(clean_name).lower()
    clean_name = str(clean_name).replace(" ",new_space)
    clean_name = str(clean_name).replace("(","")
    clean_name = str(clean_name).replace(")","")
    clean_name = str(clean_name).replace(",","")

    return clean_name




class Helper:

    helper_entry_type = None

    def set_entry_type(t):
        Helper.helper_entry_type = t

    def get_section_title_for_object(object):
        return clean_up_name(object.name)
    
    true_icons = {
        PolicyRule.Allow:":white_check_mark:",
        PolicyRule.AuditAllowed:":page_facing_up:",
        PolicyRule.Deny:":x:",
        PolicyRule.AuditDenied:":page_facing_up:"
    }

    def get_permission_icons(entry, return_objects = False):

        if entry.format == "mac":

            permission_icons = {}
            for permission in entry.all_permissions:
                enabled = permission in entry.access
                if enabled:
                    permission_icons[permission] = Helper.true_icons[entry.enforcement]
                    
                else:
                    permission_icons[permission] = "-"
                    
        else:

            permission_icons = {
            }

            masks_to_check = entry.entry_type.access_masks
            if Helper.helper_entry_type is not None:
                if hasattr(Helper.helper_entry_type,"access_masks"):
                    masks_to_check = Helper.helper_entry_type.access_masks
                elif entry.entry_type.name == "windows_device":
                    masks_to_check = Entry.WindowsDevice.access_masks
                elif entry.entry_type.name == "windows_printer":
                    masks_to_check = Entry.WindowsPrinter.access_masks
                else:
                    masks_to_check = list(WindowsEntryType.access_masks.keys())

            for mask in masks_to_check:

                if mask & int(entry.access_mask):    
                    permission_icons[mask] = Helper.true_icons[entry.enforcement]
                else:
                    permission_icons[mask] = "-" 

                

        if not return_objects:
            return permission_icons
        else:
            for permission in permission_icons:
                if permission_icons[permission] != "-":
                    permission_icons[permission] = True
                else:
                    permission_icons[permission] = False

            return permission_icons
    
    def generate_clause_table(group, return_objects = False):

        if type(group) == list:
            clauses = group
        else:
            clauses = group.clauses

        clause_table = Helper.generate_table_for_clauses(clauses, 1, return_objects)
        return clause_table

    def generate_table_for_clauses(clauses,offset=1,return_objects = False):
        table = []
        for clause in clauses:
            if len(clause.sub_clauses) > 0:
                sub_table = Helper.generate_clause_table(clause.sub_clauses,offset+1)
                for row in sub_table:
                    row = []*offset + row
                    table.append(row)
            else:
                for property in clause._properties:
                    operator = ""
                    if len(table) > 0:
                        operator = clause.clause_type
                    else:
                        operator = ""

                    if not return_objects:
                        row = ["-"]*offset + [property.name,property.value]
                    else:
                        row = ["-"]*offset + [property]

                    row[offset-1] = operator

                    table.append(row)

        return table




class Inventory:

    
    def __init__(self,source_path,generated_files_locations_by_format={},dest="."):
        self.paths = source_path
        self.generated_files_locations_by_format = generated_files_locations_by_format
        if self.generated_files_locations_by_format is None:
            self.generated_files_locations_by_format = {}
       
        self.dest_dir = dest

        group_columns = {
            "type":[],
            "path":[],
            "format":[],
            "name":[],
            "id":[],
            "match_type":[],
            "object":[],
            "type_label":[]
        }

        rule_columns = {
            "path":[],
            "format":[],
            "name":[],
            "entry_type":[],
            "id":[],
            "included_groups":[],
            "excluded_groups":[],
            "object":[],
            "rule_index":[]
        }

        rule_property_columns = {
            "ruleId": [],
            "propertyType":[],
            "propertyValue":[],
            "type":[]
        }

        rule_entry_columns = {
            "entryId":[],
            "ruleId":[],
            "enforcement":[],
            "notifications":[]
        }

        entry_parameters_columns = {
            "entryId":[],
            "ruleId":[],
            "sid":[],
            "computersid":[],
            "match_type":[]
        }

        directory_object_condition_columns = {
            "entryId":[],
            "ruleId":[],
            "objectType":[],
            "objectValue":[]
        }

        parameter_condition_columns = {
            "entryId":[],
            "ruleId":[],
            "conditionType":[],
            "conditionProperty":[],
            "conditionValue":[]
        }


        self.groups = pd.DataFrame(group_columns)
        self.policy_rules = pd.DataFrame(rule_columns)
        self.rule_properties = pd.DataFrame(rule_property_columns)

        self.rule_entries = pd.DataFrame(rule_entry_columns)
        self.entry_parameters = pd.DataFrame(entry_parameters_columns)
        self.directory_object_conditions = pd.DataFrame(directory_object_condition_columns)
        self.parameter_conditions = pd.DataFrame(parameter_condition_columns)

        self.group_property_data_frames = {}
        

        #Create the group properties tables
        for group_type in Group.AllGroupTypes:

            group_property_columns = {
               "groupId":[]
            }

            if not group_type.isWindows(): 
                group_property_columns["op"] = []
                group_property_columns["op2"] = []
                group_property_columns["op3"] = []
            

            group_properties = group_type.group_properties
            for group_property in group_properties:
                group_property_columns[group_property.label] = []

            self.group_property_data_frames[group_type.label]= pd.DataFrame(group_property_columns)

       
        self.entry_type_data_frames = {}
        for entry_type in Entry.AllEntryTypes:

            entry_type_columns = {
                "entryId":[],
                "ruleId":[]
            }

            for access_type in entry_type.access_types:
                label = entry_type.access_types[access_type]["label"]
                entry_type_columns[label] = []

            self.entry_type_data_frames[entry_type.label] = pd.DataFrame(entry_type_columns)

        self.load_inventory()

        

    def load_inventory(self):

        logger.debug("paths="+str(self.paths))
        for path in self.paths:
            logger.debug("path="+path)
            
            if str(path).endswith(".xml"):
                self.load_xml_file(path)
            elif str(path).endswith(".json"):
                self.load_json_file(path)


            for dir in os.walk(top=path):
                logger.debug("dir="+str(dir))
                files = dir[2]
                for file in files:
                    logger.debug("Attempting to load file "+str(file))
                    if str(file).endswith(".xml"):
                        xml_path = dir[0]+os.sep+file
                        self.load_xml_file(xml_path)
                    elif str(file).endswith(".json"):
                        json_path = dir[0]+os.sep+file
                        self.load_json_file(json_path)
                    else:
                        logger.warn("Unable to process file "+str(file))

    
    
    def load_json_file(self,json_path):
        try:
            with open(json_path) as file:
                json_object = json.load(file)

                if "groups" in json_object.keys():
                    group_index = 1
                    for group in json_object["groups"]:
                        self.addGroup(Group(group,"mac",json_path),group_index)
                        group_index=group_index+1

                if "rules" in json_object.keys():
                    rule_index = 1
                    for rule in json_object["rules"]:
                        self.addPolicyRule(PolicyRule(rule,"mac",json_path,rule_index))

            return
        except Exception as e:
            logger.error(full_stack())
            logger.error ("Error in "+json_path+": "+str(e))
            return
    
    def load_xml_file(self,xml_path):
        logger.debug("xml_path="+xml_path)
        try:
            with open(xml_path) as file:
                root = ET.fromstring(file.read())
                match root.tag:
                    case "Group":
                        logger.debug("Found <Group> in "+xml_path)
                        self.addGroup(Group(root,"oma-uri",xml_path))
                    case "Groups":
                        group_index = 1
                        for group in root.findall(".//Group"):
                            logger.debug("Found <Groups><Group> in "+xml_path)
                            self.addGroup(Group(group,"gpo",xml_path), group_index)
                            group_index=group_index+1
                    case "PolicyGroups":
                        #This is what Intune UX looks like on disk
                        group_index = 1
                        for group in root.findall(".//Group"):
                            logger.debug("Found <PolicyGroups><Group> in "+xml_path)
                            self.addGroup(Group(group,"gpo",xml_path), group_index)
                            group_index=group_index+1
                    case "PolicyRule":
                        logger.debug("Adding a PolicyRule - format OMA-URI")
                        self.addPolicyRule(PolicyRule(root,"oma-uri",xml_path))
                    case "PolicyRules":
                        rule_index = 1
                        for policyRule in root.findall(".//PolicyRule"):
                            logger.debug("Adding a policy rule - format GPO")
                            self.addPolicyRule(PolicyRule(policyRule,"gpo",xml_path,rule_index))
                            rule_index= rule_index + 1

                return root

        except Exception as e:
            logger.error(full_stack())
            logger.error("Error in "+xml_path+": "+str(e))
            return

    def addGroup(self,group, group_index=0):

        logger.debug("Adding group "+str(group)+" to inventory")

        path = group.path
        format = group.format

        if group.name == "?":
            paths = str(path).split(os.sep)
            last_path = paths[-1]
            fileWithoutExtension = last_path.split(".")[0]
            group.name = fileWithoutExtension+"_"+str(int(group_index))


        #Could check for Intune UX support
        new_row = pd.DataFrame([{
            "type":group.type,
            "path":path,
            "format":format,
            "name":group.name,
            "id":group.id,
            "match_type":group.match_type,
            "object": group,
            "type_label": group.group_type.label
        }])

        self.groups = pd.concat([self.groups,new_row],ignore_index=True)

    def addPolicyRule(self,rule):

        if rule.id is None:
            logger.debug("rule.id is None")
            return
        
        logger.debug("path="+rule.path+" format="+rule.format+" index="+str(rule.rule_index)+" id="+rule.id)
        
        path = rule.path
        format = rule.format
        rule_index = rule.rule_index

        


        new_row = pd.DataFrame([{
            "path":path,
            "format":format,
            "name":rule.name,
            "entry_type": rule.entry_type.label,
            "id":rule.id,
            "included_groups":rule.included_device_properties,
            "excluded_groups":rule.excluded_groups,
            "object": rule,
            "rule_index": rule_index
        }])

        self.policy_rules = pd.concat([self.policy_rules,new_row],ignore_index=True)

        for entry in rule.entries:

            new_entry = pd.DataFrame([{
                "ruleId": rule.id,
                "entryId": entry.id,
                "entry_type": entry.entry_type.label,
                "enforcement": entry.enforcement.label,
                "notifications": str(entry.notifications)
            }])

            self.rule_entries = pd.concat([self.rule_entries,new_entry],ignore_index=True)

            new_entry_permissions = {
                "entryId": entry.id,
                "ruleId": rule.id,
                "conditionMatchType": entry.get_condition_match_type()
            }

            
            permissions = Helper.get_permission_icons(entry,True)
            for permission in permissions:
                if type(permission) == int:
                    column = WindowsEntryType.access_masks[permission]
                else:
                    column = entry.entry_type.access_types[permission]["label"]

                new_entry_permissions[column] = permissions[permission]

            new_entry_permissions = pd.DataFrame([
                new_entry_permissions
            ])

            self.entry_type_data_frames[entry.entry_type.label] = pd.concat([self.entry_type_data_frames[entry.entry_type.label],new_entry_permissions],ignore_index=True)

            if entry.has_user_condition():
                  
                  new_condition = pd.DataFrame([{
                    "ruleId": rule.id,
                    "entryId": entry.id,
                    "objectType": "User",
                    "objectValue": entry.sid
                  }])

                  self.directory_object_conditions = pd.concat([self.directory_object_conditions,new_condition],ignore_index=True)

            if entry.has_computer_condition():
                  
                  new_condition = pd.DataFrame([{
                    "ruleId": rule.id,
                    "entryId": entry.id,
                    "objectType": "Computer",
                    "objectValue": entry.computersid
                  }])

                  self.directory_object_conditions = pd.concat([self.directory_object_conditions,new_condition],ignore_index=True)

            if entry.parameters is not None:
                for condition in entry.parameters.conditions:
                    for property in condition.properties:

                        new_condition = pd.DataFrame([{
                            "ruleId": rule.id,
                            "entryId": entry.id,
                            "conditionType": condition.condition_type.label,
                            "conditionProperty": property.label,
                            "conditionValue": property.value
                        }])

                        self.parameter_conditions = pd.concat([self.parameter_conditions,new_condition],ignore_index=True)


    
    def get_groups_for_rule(self,rule):
        groups_for_rule = {
            "gpo":[],
            "oma-uri":[],
            "mac":[],
            "included":[],
            "excluded":[],
            "entries":[]
        }
        for included_group in rule.included_groups:
            g = self.get_group_by_id(included_group)
            if g is not None:
                groups_for_rule["gpo"] += g["gpo"] 
                groups_for_rule["mac"] += g["mac"]
                groups_for_rule["oma-uri"] += g["oma-uri"]
                groups_for_rule["included"]+= g[rule.format]

        for excluded_group in rule.excluded_groups:
            g = self.get_group_by_id(excluded_group)
            if g is not None:
                groups_for_rule["gpo"] += g["gpo"]
                groups_for_rule["oma-uri"] += g["oma-uri"]
                groups_for_rule["mac"] += g["mac"]
                groups_for_rule["excluded"]+= g[rule.format]

        for entry in rule.entries:
            groups = entry.get_group_ids()
            for entry_group in groups:
                g = self.get_group_by_id(entry_group)
                if g is not None:
                    groups_for_rule["gpo"] += g["gpo"]
                    groups_for_rule["oma-uri"] += g["oma-uri"]
                    groups_for_rule["entries"]+= g[rule.format]
                    #Groups in entries not supported on mac
                    #groups_for_rule["mac"] += g["mac"]
        
        return groups_for_rule


    def get_group_by_id(self,group_id):
        group_frame = self.groups.query("id == '"+group_id+"'")
        if group_frame.size == 0:
            logger.warning("No group found for "+group_id)
            return None
        else:
            logger.debug("Found "+str(group_frame.size)+" group(s) for "+group_id)
            groups = {
                "gpo":[],
                "oma-uri":[],
                "mac":[]
            }
            
            for i in range(0,group_frame.index.size):
                group = group_frame.iloc[i]["object"]
                format = group_frame.iloc[i]["format"]
                path = group_frame.iloc[i]["path"]
                if group in groups[format]:
                    continue
                elif len(groups[format]) == 0:
                    logger.debug("Adding group "+str(group)+" to format")
                    groups[format].append(group)
                else:
                    logger.warning("Conflicting groups for "+group_id+" at "+path+"\n"+str(group) +"\n!=\n" +str(groups[format][0]))

            #Use either GPO or Mac, to create an OMA-URI group
            if len(groups["oma-uri"]) == 0:
                oma_uri_group = None
                if len(groups["gpo"]) > 0:
                    oma_uri_group = self.missing_oma_uri(groups["gpo"][0])
                elif len(groups["mac"]) >0:
                    oma_uri_group = self.missing_oma_uri(groups["mac"][0])

                if oma_uri_group is not None:
                    groups["oma-uri"].append(oma_uri_group)

            return groups
    
    def get_path_for_group(self,group_id):
        group_frame = self.groups.query("id == '"+group_id+"'")
        return group_frame.iloc[0]["path"]
    

    def query_policy_rules(self,query):
        rules = {
            "gpo":{},
            "oma-uri":{},
            "mac":{},
            "all":[]
        }

        query = str(query).encode('unicode-escape').decode()

        logger.debug("query="+str(query)+" class="+str(query.__class__))
        logger.debug("policy_rules="+str(self.policy_rules))

        #convert the path to string
        
        self.policy_rules['path'] = self.policy_rules['path'].astype(str)

        rule_frame = self.policy_rules.query(query, engine='python')
        rule_frame = rule_frame.sort_values("rule_index", ascending=True)

        logger.debug("query returned "+str(rule_frame.index.size)+" results.")

        for i in range(0,rule_frame.index.size):
            rule = rule_frame.iloc[i]["object"]
            logger.debug(">>>"+str(i)+" rule="+str(rule))
            format = rule_frame.iloc[i]["format"]
            rule_index = rule_frame.iloc[i]["rule_index"]
            rule_id = rule.id

            if format == "oma-uri":
                rule_id = rule.get_oma_uri()

            rules_for_format = rules[format]
            if rule_id in rules_for_format:
                existing_rule = rules_for_format[rule_id]
                if existing_rule != rule:
                    logger.warning("Conflicting rules for id "+rule.id+"\n"+str(rule)+"\n!=\n"+str(existing_rule))
            elif format == "oma-uri":
                rules[format][rule_id] = IntuneCustomRow(rule)
            else:
                rules[format][rule_id] = rule 

            rules["all"].append(rule)

        rules["all"] = list(set(rules["all"]))
        rules["all"].sort(key= lambda x: x.rule_index)

        #check for missing oma-uri rules
        for rule in rules["all"]:
            if not rule.get_oma_uri() in rules["oma-uri"]:
                oma_uri_rule = self.missing_oma_uri(rules[rule.format][rule.id])
                if oma_uri_rule is not None:
                    rules["oma-uri"][rule.get_oma_uri()] = IntuneCustomRow(oma_uri_rule)

        return rules
    

    def missing_oma_uri(self,object):
        logger.warning("Missing oma-uri for id "+object.id)
        oma_uri_object = copy.copy(object)
        oma_uri_object.format = "oma-uri"


        if "oma-uri" in self.generated_files_locations_by_format.keys():
            
            path = self.generated_files_locations_by_format["oma-uri"] + os.sep + clean_up_name(oma_uri_object.name,"_")+oma_uri_object.id + ".xml"
            oma_uri_object.set_path(path)
            with open(path,"w") as generated_file:
                generated_file.write(oma_uri_object.toXML(""))
                generated_file.close()

        else:
            oma_uri_object.set_path(None)

        return oma_uri_object
    
    def process_query(self,query=None):

        
        if query is None:
            query="path.str.contains('(.*)')"

        logger.debug("query="+query)

        filtered_rules = self.query_policy_rules(query)

        result = {}

        rules = {}
        groups = {}
        paths = []

        intune_ux_support = Support()
        windows_support = Support()
        mac_support = Support()

        groupsXML = "<Groups>"
        rulesXML  = "<PolicyRules>"
        mac_policy = {
            "groups":[],
            "rules":[]
        }
        oma_uri = filtered_rules["oma-uri"]
        entry_type = None
        has_mixed_entry_type = False

        for rule in filtered_rules["all"]:

            if rule.id in rules:
                logger.warning("Conflicting rules "+rules[rule.id].toXML()+" in "+rules[rule.id].path+" != "+rule.toXML()+" in "+rule.path)
                continue
        
            rules[rule.id] = rule
            paths.append(rule.path)
            mac_policy["rules"].append(rule.toJSON())

            #sets the entry type to the Generic
            #if the query returns more than 1
            if entry_type is None:
                entry_type = rule.entry_type
            elif entry_type is not rule.entry_type:
                if not has_mixed_entry_type:
                    if rule.format == "mac":
                        entry_type = Entry.AppleGeneric
                    else:
                        entry_type = Entry.WindowsGeneric

                    has_mixed_entry_type = True


            intune_ux_support += IntuneUXFeature.get_support_for(rule)
            windows_support += WindowsFeature.get_support_for(rule)

            rulesXML += "\n"+rule.toXML()
            groups_for_rule = self.get_groups_for_rule(rule)
            all_groups = set(groups_for_rule["gpo"]+groups_for_rule["oma-uri"])
            for group in all_groups:
                if group.id not in groups:
                    groupsXML += "\n"+group.toXML()
                    groups[group.id] = group
                    if entry_type not in Entry.MacEntryTypes:
                        paths.append(group.path)
                    mac_policy["groups"].append(group.toJSON())

                    intune_ux_support += IntuneUXFeature.get_support_for(group)
                    windows_support += WindowsFeature.get_support_for(group)
            
            for oma_uri_group in groups_for_rule["oma-uri"]:
                oma_uri[oma_uri_group.get_oma_uri()] = IntuneCustomRow(oma_uri_group)
        

        groupsXML += "\n</Groups>"
        rulesXML += "\n</PolicyRules>"

        #remove duplicates from paths
        paths = list(set(paths))
        web_paths = []
        for path in paths:
            if str(path).startswith(".\\"):
                path = path[2:]
            path = str(path).replace("\\","/")
            web_paths.append(path)

        try:
            
            mac_error = None
            if entry_type not in Entry.MacEntryTypes:
                mac_policy["groups"] = mac.convert_groups(ET.fromstring(groupsXML),True)
                mac_policy["rules"] = mac.convert_rules(ET.fromstring(rulesXML),True)
        except Exception as e:
            mac.log_error("Failed to convert policy to Mac:")
            mac.log_error(str(e))
            mac_policy = None
            mac_error = str(e)
    
        #Gather result
        result["oma_uri"] = oma_uri
        result["web_paths"] = web_paths
        result["rules"] = rules
        result["groups"] = groups
        result["intune_ux_support"] = intune_ux_support
        result["groupsXML"] = groupsXML
        result["rulesXML"] = rulesXML
        result["mac_policy"] = mac_policy
        result["mac_error"] = mac_error
        result["windows_support"] = windows_support 
        result["entry_type"] = entry_type

        return result    

    def generate_csv(self,dest):
        self.groups.to_csv(dest+os.sep+"dc_groups.csv",sep=",",index=False)
        self.policy_rules.to_csv(dest+os.sep+"dc_rules.csv",sep=",",index=False)
        self.rule_entries.to_csv(dest+os.sep+"dc_entries.csv",sep=",",index=False)
        self.directory_object_conditions.to_csv(dest+os.sep+"dc_directory_object_conditions.csv",sep=",",index=False)
        self.parameter_conditions.to_csv(dest+os.sep+"dc_parameter_conditions.csv",sep=",",index=False)
        
        #create the list of group-rule-mappings
        for i in range(0,self.policy_rules.index.size):
            rule = self.policy_rules.iloc[i]["object"]

            for property in rule.included_device_properties:
                new_row = pd.DataFrame([{
                    "type": "included",
                    "ruleId": rule.id,
                    "propertyType": property.name,
                    "propertyValue": property.value
                }])

                self.rule_properties = pd.concat([self.rule_properties,new_row],ignore_index=True)
            
            for property in rule.excluded_device_properties:
                new_row = pd.DataFrame([{
                    "type": "excluded",
                    "ruleId": rule.id,
                    "propertyType": property.name,
                    "propertyValue": property.value
                }])

                self.rule_properties = pd.concat([self.rule_properties,new_row],ignore_index=True)
            


        self.rule_properties.to_csv(dest+os.sep+"dc_rule_properties.csv",sep=",",index=False)

        #Add the group values to the dataframe
        for i in range(0,self.groups.index.size):
            new_row = {

            }
            group = self.groups.iloc[i]["object"]
            new_row["groupId"] = group.id
            if group.format != "mac":
                for property in group._properties:
                    if property.label in new_row:
                        new_row[property.label] = new_row[property.label]+", "+property.value
                    else:
                        new_row[property.label] = property.value
            else:
                clause_table = Helper.generate_clause_table(group,True)
                for clause_row in clause_table:
                    match len(clause_row):
                        case 2:
                            new_row["op"] = clause_row[0]
                        case 3:
                            new_row["op"] = clause_row[0]
                            new_row["op2"] = clause_row[1]
                        case 4:
                            new_row["op"] = clause_row[0]
                            new_row["op2"] = clause_row[1]
                            new_row["op3"] = clause_row[2]

                    clause_property = clause_row[-1]
                    new_row[clause_property.label] = clause_property.value
                    

            self.group_property_data_frames[group.group_type.label] = pd.concat([
                self.group_property_data_frames[group.group_type.label],
                pd.DataFrame([new_row])
            ],ignore_index=True)

        #save the csvs
        for group_type_label in self.group_property_data_frames:
            group_type_frame = self.group_property_data_frames[group_type_label]
            group_type_file_name_part = str(group_type_label).lower().replace(" ","_")
            group_type_frame.to_csv(dest+os.sep+"dc_"+group_type_file_name_part+".csv",sep=",",index=False)

        for entry_type_label in self.entry_type_data_frames:
            entry_type_frame = self.entry_type_data_frames[entry_type_label]
            entry_type_file_name_part = str(entry_type_label).lower().replace(" ","_")
           
            entry_type_frame.to_csv(dest+os.sep+"dc_"+entry_type_file_name_part+"_access.csv",sep=",",index=False)

            
        



    def generate_text(self,result,template,dest,file,title, settings = None ):
        
        if settings is not None:
            custom_settings_values = settings.getIntuneCustomValues()
            for custom_settings_value in custom_settings_values:
                result["oma_uri"][custom_settings_value] = custom_settings_values[custom_settings_value] 

            if result["mac_policy"] is not None:
                mac_policy = result["mac_policy"]
                mac_policy["settings"] = settings.get_mac_settings()
                result["mac_policy"] = mac_policy


        
        if result["mac_policy"] is not None:
            result["mac_policy"] = json.dumps(result["mac_policy"],indent=4)


        #Make web_paths relative to output file
        dest_path = pathlib.PurePath(dest)
        rel_web_paths = []
        for web_path in result["web_paths"]:
            rel_path = os.path.relpath(web_path,str(dest_path))
            rel_web_paths.append(rel_path)

        result["web_paths"] = rel_web_paths


        Helper.set_entry_type(result["entry_type"])

        logger.debug("Rendering results with template "+str(template)+" to "+str(dest)+os.sep+str(file))

        params = {"intuneCustomSettings":result["oma_uri"],
             "paths":result["web_paths"],
             "rules":result["rules"],
             "groups":result["groups"], 
             "intune_ux_support":result["intune_ux_support"],
             "windows_support": result["windows_support"],
             "groupsXML": result["groupsXML"], 
             "rulesXML":result["rulesXML"],
             "macPolicy":result["mac_policy"],
             "macError": result["mac_error"],
             "entry_type": result["entry_type"],
             "description": result["description"],
             "settings": settings,
             "env":os.environ,
             "Helper":Helper,
             "title":title}
        
        logger.debug("params="+str(params))
        out = template.render(
            params)
        
        logger.debug("out="+str(out))
        out_file_name = dest+os.sep+file
        with open(out_file_name,"w") as out_file:
            out_file.write(out)
            out_file.close()

        logger.info("Generated documentation "+str(pathlib.Path(out_file_name).resolve()))

class Description:

    def __init__(self,result,templateEnv,description_template_name):
        self.result = result
        self.template = templateEnv.get_template(description_template_name)

        

    def __str__(self):
        return self.template.render({
            "result":self.result
        })

def dir_path(string):
    paths = string.split(os.pathsep)
    for path in paths:
        if os.path.isdir(path):
            continue
        else:
            raise NotADirectoryError(path)
    return paths

def dir(path):
    if os.path.isdir(path):
        return path
    else:
        raise NotADirectoryError(path)

def file(path):
    if os.path.isfile(path):
        return path
    else:
        raise argparse.ArgumentError("Not a file "+path)
    

def path_array(path):
    paths = []
    path_strs = str.split(path,os.pathsep)
    for path_str in path_strs:
        
        path = pathlib.Path(path_str)
        if path.is_absolute():
            paths.append(path)
        else:
            parent_path = pathlib.Path(__file__ ).parent.resolve() 
            path = pathlib.Path(str(parent_path)+ os.sep + path_str).resolve()
            paths.append(path)

    return paths

def format(string):
    match string:
        case "text":
            return string
        case "csv":
            return string
        case other:
            raise argparse.ArgumentError("Invalid format "+string)

def generate_files_format(format_strings):

    generated_files_locations_by_format = {}
    format_string = format_strings.split(",")
    for format_string in format_string:
        
        format_string_values = format_string.split(":")
        format = format_string_values[0]
        location = ":".join(format_string_values[1:])

        if format in ["oma-uri","mac","gpo"]:
            if os.path.isdir(location):
                generated_files_locations_by_format[format] = location
                continue
            else:
                raise argparse.ArgumentError("Invalid location to save generated files"+location)

        else:
            raise argparse.ArgumentError("Invalid format "+format)

    return generated_files_locations_by_format

def parse_in_file(in_file):
    #query = "path.str.contains('"+in_file+"',regex=False)"
    query = "path == '"+in_file+"'"
    title = str(in_file.split(os.sep)[-1]).split(".")[0]
    outfile = title+".md"
    settings = Default_Settings

    #check the settings for the file
    if in_file.endswith(".json"):
         p = pathlib.PurePath(in_file)
         with open(in_file,"r") as json_file:
            mac_policy = json.loads(json_file.read())
            mac_settings = Settings.generate_settings_from_mac_policy(mac_policy)
            if mac_settings is not None:
                settings = mac_settings

            json_file.close()
            

    return query,title,outfile,settings

def load_scenarios(scenarios_file):
     with open(scenarios_file) as file:
         scenarios = json.loads(file.read())
         return scenarios
     
def generate_readme(results,templateEnv,dest,title,readme_template,readme_file,templates_path):

    template = templateEnv.get_template(readme_template)
    out = template.render(
        {
            "results":results,
            "dest":dest,
            "title":title,
            "env":os.environ
         }
    )


    if pathlib.Path.is_absolute(pathlib.Path(readme_file)):
        readme_file_path = readme_file
    else:
        readme_file_path = dest+os.sep+readme_file

    with open(readme_file_path,"w") as out_file:
        out_file.write(out)
        out_file.close()

    logger.info("Generated README "+str(pathlib.Path(readme_file_path).resolve()))

def process_args(args):

    import logging.config
    logging.config.fileConfig(args.loggingConf)
    
    templateLoader = jinja2.FileSystemLoader(searchpath=args.templates_path)
    templateEnv = jinja2.Environment(loader=templateLoader)

    inventory = Inventory(args.source_path,args.generated_files_locations,args.dest)

    query = args.query
    title = None
    if "TITLE" in os.environ.keys():
        title = os.environ["TITLE"]

    out_file = args.out_file

    if args.scenarios is not None:

        scenarios_dir = os.path.dirname(args.scenarios)

        results = {}
        scenarios = load_scenarios(args.scenarios)
        for rule in scenarios["scenarios"]:
            policy_file = rule["file"]

            policy_path = pathlib.Path(os.path.join(scenarios_dir,policy_file)).resolve()
            policy_file = None
            for source_path in args.source_path:
                try:
                    policy_path = policy_path.relative_to(source_path)
                    logger.debug(str(policy_path)+" is relative to "+source_path)
                    policy_file = os.path.join(source_path,policy_path)
                    break
                except ValueError as e:
                    logger.info(str(e))

            if policy_file is None:
                logger.warning("Policy file in "+rule["file"]+" wasn't found in "+str(args.source_path))
                continue
            
            title = None
            description = None

            if "description" in rule.keys():
                description = rule["description"]
            
            if "title" in rule.keys():
                title = rule["title"]
            
            logger.debug("Generating parameters for "+policy_file)
            query,default_title,default_outfile,default_settings = parse_in_file(policy_file)
            if "settings" in rule.keys():
                settings = Settings(rule["settings"])
            else:
                settings = default_settings
            
            result = inventory.process_query(query)
            if args.format == "text":
                if title is None:
                    title = default_title

                if description is not None:
                    result["description"] = description
                else:
                    result["description"] = Description(result,templateEnv, args.description_template)

                TEMPLATE_FILE = args.template
                template = templateEnv.get_template(TEMPLATE_FILE)

                inventory.generate_text(result,template,args.dest,default_outfile,title,settings)

            results[policy_file] = {
                "result":result,
                "title": title,
                "file": default_outfile
            }

        generate_readme(results,templateEnv,args.dest,scenarios["title"],args.readme_template, args.readme_file, args.templates_path)


    elif query is None:
        if args.in_file is not None:
            query,title,default_outfile,settings = parse_in_file(args.in_file)
            if out_file is None:
                out_file = default_outfile

        if args.format == "text":
            result = inventory.process_query(query)

            result["description"] = Description(result,templateEnv,args.description_template)

            TEMPLATE_FILE = args.template
            template = templateEnv.get_template(TEMPLATE_FILE)

            inventory.generate_text(result,template,args.dest,out_file,title,settings)
        elif args.format == "csv":
            inventory.generate_csv(args.dest)
        
    

def main():
    arg_parser = argparse.ArgumentParser(
        description='Utility for generating documentation for device control policies.')

    
    input_group =arg_parser.add_mutually_exclusive_group()
    input_group.add_argument('-q','--query',dest="query",help='The query to retrieve the policy rules to process')
    input_group.add_argument('-s','--scenarios',dest="scenarios",type=file,help='A JSON file that contains a list of scenarios to process')
    input_group.add_argument('-i','--input',dest="in_file",type=file,help='A policy rule to process')

    arg_parser.add_argument('-l','--loggingConf', type=file,dest="loggingConf",help="path to the logging.conf",default="logging.conf")


    arg_parser.add_argument('-p', '--path', type=dir_path, dest="source_path", help='The path to search for source files.  Defaults to current working directory.',default=".")
    arg_parser.add_argument('-f','--format',type=format, dest="format",help="The format of the output.  Defaults to text.",default="text")
    arg_parser.add_argument('-o','--output',dest="out_file",help="The output file")
    arg_parser.add_argument('-d','--dest',dest="dest",type=dir,help="The output directory.  Defaults to current working directory.",default=".")
    arg_parser.add_argument('-g','--generate',dest="generated_files_locations", type=generate_files_format, help='Generates files for other formats')
    
    arg_parser.add_argument('-t','--template',dest="template",help="Jinja2 template to use to generate output.  Defaults to dcutil.j2.",default="dcutil.j2")
    arg_parser.add_argument('-rt','--readme_template',dest="readme_template",help="Jinja2 template to use for the readme.  Defaults to readme.j2.",default="readme.j2")
    arg_parser.add_argument('-dt','--description_template',dest="description_template",help="Jinja2 template to use for the description.  Defaults to description.j2.",default="description.j2")
    arg_parser.add_argument('-r','--readme',dest="readme_file",help="The readme file to generate.  Defaults to readme.md.",default="readme.md")
    arg_parser.add_argument('-tp','--templates_path',dest="templates_path",help="path to Jinja2 templates.  Defaults to templates.",default="templates",type=path_array)
    

    args = arg_parser.parse_args()

    
    process_args(args)

if __name__ == '__main__':
    main()