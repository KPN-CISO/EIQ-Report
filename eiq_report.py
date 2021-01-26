#!/usr/bin/env python3

# (c) 2021 Arnim Eijkhoudt <arnime _squigglything_ kpn-cert.nl>

# This software is GPLv3 licensed, except where otherwise indicated

import argparse
import collections
import fastapi
import io
import json
import pandas as pd
import pprint
import re
import smtplib
import ssl
import sys
import time
import uvicorn


from config.ActorTable import ActorTable
from config.AlertTable import AlertTable
from config import settings
from eiqlib import eiqcalls
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from os.path import basename
from typing import Optional


class DummyArgs(object): # Ugly hook to prevent missing argparser options from messing up FastAPI
    pass

app = FastAPI(title='Management Statistics API')

@app.get('/')
def read_root():
    return {'FastAPI Error': '401 Unauthorized', 'Reason': 'Go away, or I will replace you with a very small shell script.'}

@app.get('/mgmtstats')
def read_item():
    return {'FastAPI Error': '401 Unauthorized', 'Reason': 'Go away, or I will replace you with a very small shell script.'}

@app.get('/mgmtstats/{feedID}')
def read_item(feedID: int, magictoken: Optional[str]):
    if magictoken != settings.MAGICTOKEN:
        return {'FastAPI Error': '403 Unauthorized', 'Reason': 'Missing or incorrect token.'}
    else:
        if not feedID:
            return {'FastAPI Error': '403 Unauthorized', 'Reason': 'Missing or incorrect feed ID.'}
        else:
            options = DummyArgs()
            options.verbose = False
            options.simulate = False
            options.daemonize = True
            feedDict = download(feedID, options)
            if feedDict:
                mgmtstats = dict()
                mgmtstats['alertmap'], mgmtstats['actormap'] = transform(feedDict, feedID, options)
                if mgmtstats:
                    return JSONResponse(content=json.dumps(mgmtstats))

@app.get('/mgmtstats/alertmap/{feedID}')
def read_item(feedID: int, magictoken: Optional[str]):
    if magictoken != settings.MAGICTOKEN:
        return {'FastAPI Error': '403 Unauthorized', 'Reason': 'Missing or incorrect token.'}
    else:
        if not feedID:
            return {'FastAPI Error': '403 Unauthorized', 'Reason': 'Missing or incorrect feed ID.'}
        else:
            options = DummyArgs()
            options.verbose = False
            options.simulate = False
            options.daemonize = True
            feedDict = download(feedID, options)
            if feedDict:
                mgmtstats = dict()
                mgmtstats['alertmap'], mgmtstats['actormap'] = transform(feedDict, feedID, options)
                if mgmtstats:
                    alertmaptable = pd.read_json(json.dumps(mgmtstats['alertmap'], sort_keys=True), orient='index')
                    alertmaptable = alertmaptable.reindex(columns=['title', 'count', 'description'])
                    alertmaptable = alertmaptable[['count', 'description']]
                    blob = io.BytesIO()
                    alertmaptable.to_csv(blob)
                    blob.seek(0)
                    return Response(content=blob.getvalue(), media_type='text/plain')

@app.get('/mgmtstats/actormap/{feedID}')
def read_item(feedID: int, magictoken: Optional[str]):
    if magictoken != settings.MAGICTOKEN:
        return {'FastAPI Error': '403 Unauthorized', 'Reason': 'Missing or incorrect token.'}
    else:
        if not feedID:
            return {'FastAPI Error': '403 Unauthorized', 'Reason': 'Missing or incorrect feed ID.'}
        else:
            options = DummyArgs()
            options.verbose = False
            options.simulate = False
            options.daemonize = True
            feedDict = download(feedID, options)
            if feedDict:
                mgmtstats = dict()
                mgmtstats['alert'], mgmtstats['actormap'] = transform(feedDict, feedID, options)
                if mgmtstats:
                    actortable = pd.read_json(json.dumps(mgmtstats['actormap'], sort_keys=True), orient='index')
                    actortable = actortable.reindex(columns=['count', 'description', 'altnames'])
                    blob = io.BytesIO()
                    actortable.to_csv(blob)
                    blob.seek(0)
                    return Response(content=blob.getvalue(), media_type='text/plain')


def transform(feedJSONs, feedID, options):
    '''
    Take the EIQ JSON objects, extract all observables into lists,
    and transform those into the selected ruletypes.
    '''
    if options.verbose:
        print("U) Converting EIQ JSON objects into a group of entities ...")
    entities = []
    alertmap = createAlertMap(options)
    actormap = createActorMap(options)
    for feedJSON in feedJSONs:
        for entity in feedJSON:
            if 'description' in entity['data']: # Check if actual alert w/ contents
                actor = 'unknown'
                description = entity['data']['description']
                title = entity['meta']['title']
                mapAlert(title, description, alertmap, options)
                for extract in entity['extracts']: # Check for a known actor
                    if extract['kind'] == 'actor':
                        actor = extract['value']
                mapActor(actor, actormap, options)
    return(alertmap, actormap)


def createAlertMap(options, alertmap = dict()):
    for alerttype in AlertTable.keys():
        alertmap[alerttype] = dict()
        alertmap[alerttype]['count'] = 0
        alertmap[alerttype]['title'] = AlertTable[alerttype]['title']
        if AlertTable[alerttype].get('description'):
            alertmap[alerttype]['description'] = AlertTable[alerttype]['description']
    return alertmap


def createActorMap(options, actormap = dict()):
    for actorname in ActorTable.keys():
        actormap[actorname] = dict()
        actormap[actorname]['count'] = 0
        actormap[actorname]['altnames'] = ActorTable[actorname]['altnames']
        if ActorTable[actorname].get('description'):
            actormap[actorname]['description'] = ActorTable[actorname]['description']
    return actormap


def mapAlert(title, description, alertmap, options):
    for alerttype in AlertTable.keys():
        titlematches = AlertTable[alerttype]['titlematch']
        alerttitle = AlertTable[alerttype]['title']
        if AlertTable[alerttype].get('descmatch'):
            descmatches = AlertTable[alerttype]['descmatch']
        else:
            descmatches = None
        for titlematch in titlematches:
            titlematchre = re.compile(titlematch, re.IGNORECASE)
            result = titlematchre.search(title)
            if result:
                if descmatches:
                    for descmatch in descmatches:
                        descmatchre = re.compile(descmatch, re.IGNORECASE)
                        result = descmatchre.search(description)
                        if result: # Hit on a description match
                            alertmap[alerttype]['count'] += 1
                else: # No separate description matching needed
                    alertmap[alerttype]['count'] += 1


def mapActor(actor, actormap, options):
    for actorname in ActorTable.keys():
        actoraltnames = ActorTable[actorname]['altnames']
        for actoraltname in actoraltnames:
            if actoraltname.lower() == actor.lower():
                actormap[actorname]['count'] += 1


def download(feedID, options):
    '''
    Download the given feed number from the EclecticIQ JSON instance
    '''
    if not settings.EIQSSLVERIFY:
        if options.verbose:
            print("W) You have disabled SSL verification for EIQ, " +
                  "this is not recommended.")
    eiqAPI = eiqcalls.EIQApi(insecure=not(settings.EIQSSLVERIFY))
    eiqHost = settings.EIQHOST + settings.EIQVERSION
    eiqFeed = settings.EIQFEEDS + '/' + str(feedID) + '/runs/latest'
    eiqAPI.set_host(eiqHost)
    eiqAPI.set_credentials(settings.EIQUSER, settings.EIQPASS)
    eiqToken = eiqAPI.do_auth()
    eiqHeaders = {}
    eiqHeaders['Authorization'] = 'Bearer %s' % (eiqToken['token'],)
    try:
        if options.verbose:
            print("U) Contacting " + eiqHost + eiqFeed + ' ...')
        response = eiqAPI.do_call(endpt=eiqFeed,
                                  headers=eiqHeaders,
                                  method='GET')
    except IOError:
        print("E) An error occurred contacting the EIQ URL at " +
              eiqHost + eiqFeed)
        raise
    if not response or ('errors' in response):
        if response:
            for err in response['errors']:
                print('[error %d] %s' % (err['status'], err['title']))
                print('\t%s' % (err['detail'], ))
        else:
            print('unable to get a response from host')
            sys.exit(1)
    if 'content_blocks' not in response['data']:
        if options.verbose:
            print("E) No content blocks in feed ID!")
    else:
        if options.verbose:
            print("U) Attempting to download latest feed content ...")
        block = 0
        entities = list()
        for block, value in enumerate(response['data']['content_blocks']):
            content_block = response['data']['content_blocks'][block]
            content_block = content_block.replace(settings.EIQVERSION, "")
            jsonresponse = eiqAPI.do_call(endpt=content_block,
                                          headers=eiqHeaders,
                                          method='GET')
            if options.verbose:
                pprint.pprint(jsonresponse)
            entities.append(jsonresponse['entities'])
        if options.verbose:
            pprint.pprint(entities)
        return(entities)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='EIQ to DATP converter')
    parser.add_argument('-v', '--verbose',
                        dest='verbose',
                        action='store_true',
                        default=False,
                        help='[optional] Enable progress/error info ' +
                             '(default: disabled)')
    parser.add_argument('-s', '--simulate',
                        dest='simulate',
                        action='store_true',
                        default=False,
                        help='[optional] Do not actually generate anything, ' +
                             'just simulate everything. Mostly useful with ' +
                             'the -v/--verbose flag for debugging purposes.')
    parser.add_argument('-d', '--daemonize',
                        dest='daemonize',
                        action='store_true',
                        default=False,
                        help='[optional] Run as a webserver.')
    parser.add_argument('-f', '--feedID',
                        dest='feedID',
                        required=False,
                        default=None,
                        help='[required] The ID of the EclecticIQ feed to ' +
                             'ingest')
    options = parser.parse_args()
    if not options.daemonize:
        if not options.feedID:
            print("E) A feed ID is mandatory (-f option) when running interactively, try -h for help!")
        else:
            try:
                feedID = int(options.feedID)
            except ValueError:
                print("E) Please specify a numeric feedID!")
                raise
            feedDict = download(feedID, options)
            if feedDict:
                mgmtstats = dict()
                mgmtstats['alertmap'], mgmtstats['actormap'] = transform(feedDict, feedID, options)
                if mgmtstats:
                    alertmaptable = pd.read_json(json.dumps(mgmtstats['alertmap'], sort_keys=True), orient='index')
                    actormaptable = pd.read_json(json.dumps(mgmtstats['actormap'], sort_keys=True), orient='index')
                    alertmaptable = alertmaptable.reindex(columns=['count', 'title', 'description'])
                    actormaptable = actormaptable.reindex(columns=['count', 'description', 'altnames'])
                    if not options.simulate and settings.WRITEFILES:
                        alertmaptable.to_csv(settings.ALERTFILE)
                        actormaptable.to_csv(settings.ACTORFILE)
                    if options.verbose:
                        print("---\nI) Alert Map")
                        print(alertmaptable)
                        print("---\nI) Actor Map")
                        print(actormaptable)
                    if not options.simulate and settings.EMAILSEND:
                        msg = MIMEMultipart()
                        msg['Subject'] = settings.EMAILSUBJECT
                        msg['From'] = settings.EMAILFROM
                        msg['To'] = settings.EMAILTO
                        msg['Date'] = formatdate()
                        msg['Message-Id'] = make_msgid()
                        content = '<html><head><title>'
                        content += settings.EMAILSUBJECT
                        content += '</title></head><body>'
                        content += '<p>This email contains the ' + settings.EMAILSUBJECT + '</p>'
                        content += '</body></html>'
                        msg.attach(MIMEText(content, 'html'))
                        alertmapblob = io.BytesIO()
                        alertmaptable.to_csv(alertmapblob)
                        alertmapblob.seek(0)
                        actormapblob = io.BytesIO()
                        actormaptable.to_csv(actormapblob)
                        actormapblob.seek(0)
                        part = MIMEApplication(
                            alertmapblob.read(),
                            Name=basename(settings.ALERTFILE)
                        )
                        part['Content-Disposition'] = 'attachment; filename="%s"' % basename(settings.ALERTFILE)
                        msg.attach(part)
                        part = MIMEApplication(
                            actormapblob.read(),
                            Name=basename(settings.ACTORFILE)
                        )
                        part['Content-Disposition'] = 'attachment; filename="%s"' % basename(settings.ACTORFILE)
                        msg.attach(part)
                        smtp = smtplib.SMTP(settings.EMAILSERVER)
                        smtp.send_message(msg)
                    else:
                        print("W) Not sending e-mail because we are in simulation mode!")
    else:
        uvicorn.run('eiq_report:app', host=settings.HOST, port=settings.PORT, log_level='info', reload=True)
