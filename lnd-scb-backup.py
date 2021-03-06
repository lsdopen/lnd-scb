import codecs
import configparser
import datetime
import grpc
import logging
import os
import rpc_pb2 as ln
import rpc_pb2_grpc as lnrpc
import sys
import time

from base64 import b64decode
from google.cloud import storage
from google.protobuf.json_format import MessageToDict
from str2bool import str2bool

def getConfig(conf='lnd-scb-backup.conf', section='backup'):
    config = configparser.ConfigParser()
    config.read(conf)
    return {k:v for k, v in config[section].items()}

def connect():
    config = getConfig()

    with open(config['readonlymacaroonpath'], 'rb') as f:
        macaroon_bytes = f.read()
        macaroon = codecs.encode(macaroon_bytes, 'hex')

    os.environ["GRPC_SSL_CIPHER_SUITES"] = 'HIGH+ECDSA'

    cert = open(config['tlscertpath'], 'rb').read()
    creds = grpc.ssl_channel_credentials(cert)
    channel = grpc.secure_channel(config['lndrpchost'] + ':' + config['lndrpcport'], creds)
    stub = lnrpc.LightningStub(channel)

    # Simple test to query WalletBalance (There is probably a better test)
    if stub.WalletBalance(ln.WalletBalanceRequest(), metadata=[('macaroon', macaroon)]):
        logger.info(f'Connection to LND on {config["lndrpchost"]}:{config["lndrpcport"]} successful')

    return stub, macaroon

def subscribe(connection, macaroon):
    config = getConfig()

    stub = connection
    request = ln.ChannelBackupSubscription()

    logger.info("Subscribing to and waiting for LND multiChanBackup updates")
    if not str2bool(config['test']):
        for response in stub.SubscribeChannelBackups(request, metadata=[('macaroon', macaroon)]):
            json_out = MessageToDict(response, including_default_value_fields=True)
            multiChanBackup = b64decode(json_out["multiChanBackup"]["multiChanBackup"])
            backupChannel(multiChanBackup)

            # Binary to hex if you wanted the hex value
            # hex = decode.hex()
            # print(hex)
    else:
        logger.warning('Running in test mode (test=true) and simulating a multichanbackup binary write')
        backupChannel(b'abc123')
        time.sleep(10)

def backupChannel(multiChanBackup):
    config = getConfig()

    date = datetime.datetime.now()
    datefile = date.strftime("%Y-%m-%d-%X")

    for method in config['method'].split(','):
        if method == 'file':
            cfg = getConfig(section='file')
            filename = cfg['filepath'] + '/' + cfg['filename'] + '-' + datefile + '.backup'
            if not os.path.exists(cfg['filepath']):
                logger.error(f'Backup filepath {cfg["filepath"]} does not exist. Attemping to create.')
                os.makedirs(cfg['filepath'])

            with open(filename, "wb") as file:
                file.write(multiChanBackup)
            logger.info(f'Created multiChanBackup to file {filename}')

        elif method == 'bucket':
            cfg = getConfig(section='bucket')

            storage_client = storage.Client.from_service_account_json(cfg['credspath'])
            try:
                bucket = storage_client.get_bucket(cfg['bucket'])
            except:
                logger.error('Failed to find Google Bucket')
                logger.error('Known buckets are:')
                buckets = storage_client.list_buckets()
                for bucket in buckets:
                    logger.error(bucket.name)

            # Create tempory file for upload
            filename = cfg['filename'] + '-' + datefile + '.backup'
            with open(filename, "wb") as file:
                file.write(multiChanBackup)
            
            blob = bucket.blob(filename)
            blob.upload_from_filename(filename)
            logger.info(f'Created multiChanBackup on Google Bucket: ' + blob.public_url)
            os.remove(filename)
        else:
            logger.error('No backup methods specified')

def verifyBackup():
    #TODO
    ''' Function to verify backup by passing the filename to "lncli verifychanbackup --multi_file filename" '''
    pass

#Global logging defintion
config = getConfig()
logger = logging.getLogger()
loglevel = config['loglevel'].upper()
logfile = config['logfile']

logger.setLevel(loglevel)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
fh = logging.FileHandler(logfile)
sh = logging.StreamHandler(sys.stdout)
fh.setFormatter(formatter)
sh.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(sh)

def main():
    logger.info('Application Started')
    connection, macaroon = connect()
    while True:
        subscribe(connection, macaroon)

if __name__ == "__main__":
    main()
