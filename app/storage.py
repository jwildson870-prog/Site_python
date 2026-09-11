import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import quote

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError
from flask import current_app


class StorageError(Exception):
    """Erro seguro e amigável ao acessar o armazenamento."""

    def __init__(self, message, *, code='storage_error', technical=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.technical = technical


def _env(name):
    return os.getenv(name, '').strip()


def _key_id():
    # Aceita o nome usado no Render e o nome tradicional do projeto.
    return _env('B2_APPLICATION_KEY_ID') or _env('B2_KEY_ID')


def _endpoint():
    # O endpoint deve ser o endpoint S3 da região, sem o nome do bucket.
    endpoint = _env('B2_ENDPOINT') or 'https://s3.us-east-005.backblazeb2.com'
    return endpoint.rstrip('/')


def b2_enabled():
    return bool(_key_id() and _env('B2_APPLICATION_KEY') and _env('B2_BUCKET_NAME'))


def b2_configuration_message():
    """Retorna uma mensagem amigável quando a configuração do B2 está incompleta."""
    if not b2_enabled():
        missing = [
            name for name in (
                'B2_APPLICATION_KEY_ID/B2_KEY_ID',
                'B2_APPLICATION_KEY',
                'B2_BUCKET_NAME',
            ) if not _env(name)
        ]
        return f'Backblaze B2 não está configurado corretamente. Variáveis ausentes: {", ".join(missing)}.'
    return None


def _client():
    message = b2_configuration_message()
    if message:
        raise StorageError(message, code='storage_config')

    endpoint = _endpoint()
    if not endpoint.startswith(('http://', 'https://')):
        raise StorageError(
            'O endereço do Backblaze B2 está configurado incorretamente. Verifique B2_ENDPOINT.',
            code='storage_config',
        )

    # O Backblaze B2 S3 exige assinatura V4. Path-style também é suportado
    # pelo B2 e evita problemas de DNS/certificado com nomes de bucket.
    return boto3.client(
        's3',
        endpoint_url=endpoint.rstrip('/'),
        aws_access_key_id=_key_id(),
        aws_secret_access_key=_env('B2_APPLICATION_KEY'),
        region_name=_env('B2_REGION') or 'us-east-005',
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )


def _bucket():
    bucket = _env('B2_BUCKET_NAME')
    if not bucket:
        # O bucket criado para este projeto é SitePython.
        bucket = 'SitePython'
    # Compatibilidade com a configuração antiga do projeto: ela usava
    # "SitPython" (sem o segundo "e"), mas o bucket real é "SitePython".
    # Não alteramos outros nomes configurados pelo usuário.
    if bucket.casefold() == 'sitpython':
        bucket = 'SitePython'
    return bucket


def _native_error(response, action='acessar o Backblaze B2'):
    try:
        data = response.json()
    except ValueError:
        data = {}
    code = str(data.get('code') or response.status_code).strip()
    message = str(data.get('message') or response.text or '').strip()
    if response.status_code == 401 or code in {'unauthorized', 'bad_auth_token', 'expired_auth_token'}:
        return StorageError(
            'A chave do Backblaze não foi autorizada. Crie/edite uma Application Key com permissão de escrita no bucket.',
            code='storage_credentials',
            technical=f'{code}: {message}',
        )
    if response.status_code == 403 or code == 'storage_cap_exceeded':
        return StorageError(
            'O Backblaze recusou o acesso por permissão ou limite da conta. Verifique a permissão writeFiles da Application Key.',
            code='storage_unauthorized',
            technical=f'{code}: {message}',
        )
    if code in {'bad_bucket_id', 'no_such_bucket', 'bucket_not_found'}:
        return StorageError(
            'O bucket configurado não foi encontrado ou a Application Key não tem acesso a ele. Confira B2_BUCKET_NAME.',
            code='storage_bucket_not_found',
            technical=f'{code}: {message}',
        )
    if response.status_code >= 500:
        return StorageError(
            f'O Backblaze não conseguiu {action} agora. Tente novamente em alguns segundos.',
            code='storage_unavailable',
            technical=f'{code}: {message}',
        )
    return StorageError(
        f'Não foi possível {action}. O Backblaze retornou {code}: {message or "erro desconhecido"}.',
        code='storage_error',
        technical=f'{code}: {message}',
    )


def _native_authorize():
    """Autoriza a Application Key pela API nativa do B2 (v4)."""
    if not b2_enabled():
        message = b2_configuration_message()
        raise StorageError(message or 'Backblaze B2 não configurado.', code='storage_config')
    try:
        raw = f'{_key_id()}:{_env("B2_APPLICATION_KEY")}'.encode('utf-8')
        basic = base64.b64encode(raw).decode('ascii')
        response = requests.get(
            'https://api.backblazeb2.com/b2api/v4/b2_authorize_account',
            headers={'Authorization': f'Basic {basic}'},
            timeout=(10, 30),
        )
    except requests.RequestException as exc:
        raise StorageError(
            'Não foi possível conectar à API do Backblaze B2 para autenticar a aplicação.',
            code='storage_unavailable',
            technical=str(exc),
        ) from exc
    if not response.ok:
        raise _native_error(response, 'autenticar no Backblaze B2')
    try:
        data = response.json()
    except ValueError as exc:
        raise StorageError('O Backblaze retornou uma resposta inválida ao autenticar.', code='storage_unavailable', technical=str(exc)) from exc
    storage_api = ((data.get('apiInfo') or {}).get('storageApi') or {})
    api_url = str(storage_api.get('apiUrl') or '').rstrip('/')
    token = str(data.get('authorizationToken') or '')
    if not api_url or not token:
        raise StorageError('O Backblaze não retornou os dados necessários para autenticação.', code='storage_unavailable')
    return data, api_url, token


def _native_bucket_id(data, api_url, token):
    bucket_name = _bucket()
    allowed = (((data.get('apiInfo') or {}).get('storageApi') or {}).get('allowed') or {})
    allowed_buckets = allowed.get('buckets') or []
    for bucket in allowed_buckets:
        if str(bucket.get('name') or '').casefold() == bucket_name.casefold():
            return bucket.get('id')

    # Para uma key não restrita, ou uma key que permite listBuckets, peça apenas
    # o bucket informado. Isso evita depender da API S3 para descobrir o bucket.
    try:
        response = requests.post(
            f'{api_url}/b2api/v4/b2_list_buckets',
            headers={'Authorization': token},
            json={'accountId': data.get('accountId'), 'bucketName': bucket_name},
            timeout=(10, 30),
        )
    except requests.RequestException as exc:
        raise StorageError('Não foi possível consultar o bucket no Backblaze B2.', code='storage_unavailable', technical=str(exc)) from exc
    if not response.ok:
        raise _native_error(response, 'consultar o bucket')
    buckets = response.json().get('buckets') or []
    for bucket in buckets:
        if str(bucket.get('bucketName') or '').casefold() == bucket_name.casefold():
            return bucket.get('bucketId')
    raise StorageError(
        f'O bucket "{bucket_name}" não foi encontrado ou a Application Key não tem acesso a ele.',
        code='storage_bucket_not_found',
    )


def _native_upload(file_storage, key, content_type):
    data, api_url, token = _native_authorize()
    bucket_id = _native_bucket_id(data, api_url, token)
    try:
        upload_response = requests.get(
            f'{api_url}/b2api/v4/b2_get_upload_url',
            headers={'Authorization': token},
            params={'bucketId': bucket_id},
            timeout=(10, 30),
        )
    except requests.RequestException as exc:
        raise StorageError('Não foi possível obter o endereço de upload do Backblaze B2.', code='storage_unavailable', technical=str(exc)) from exc
    if not upload_response.ok:
        raise _native_error(upload_response, 'preparar o upload')
    try:
        upload_info = upload_response.json()
        upload_url = upload_info['uploadUrl']
        upload_token = upload_info['authorizationToken']
    except (ValueError, KeyError) as exc:
        raise StorageError('O Backblaze retornou um endereço de upload inválido.', code='storage_unavailable', technical=str(exc)) from exc

    stream = getattr(file_storage, 'stream', file_storage)
    try:
        stream.seek(0)
        position = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(position)
        if position != 0:
            stream.seek(0)
        digest = hashlib.sha1()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        sha1 = digest.hexdigest()
        stream.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise StorageError('Não foi possível preparar o arquivo para o upload.', code='storage_error', technical=str(exc)) from exc

    headers = {
        'Authorization': upload_token,
        'X-Bz-File-Name': quote(key, safe='/'),
        'Content-Type': content_type or 'b2/x-auto',
        'Content-Length': str(size),
        'X-Bz-Content-Sha1': sha1,
    }
    try:
        response = requests.post(upload_url, headers=headers, data=stream, timeout=(10, 120))
    except requests.RequestException as exc:
        raise StorageError('Não foi possível enviar o arquivo ao Backblaze B2.', code='storage_unavailable', technical=str(exc)) from exc
    if response.ok:
        return key
    # A URL de upload pode expirar/rejeitar a operação; refaça a obtenção uma vez.
    if response.status_code >= 500:
        try:
            upload_response = requests.get(
                f'{api_url}/b2api/v4/b2_get_upload_url',
                headers={'Authorization': token},
                params={'bucketId': bucket_id},
                timeout=(10, 30),
            )
            if upload_response.ok:
                info = upload_response.json()
                stream.seek(0)
                response = requests.post(
                    info['uploadUrl'],
                    headers={**headers, 'Authorization': info['authorizationToken']},
                    data=stream,
                    timeout=(10, 120),
                )
                if response.ok:
                    return key
        except (requests.RequestException, KeyError, ValueError):
            pass
    raise _native_error(response, 'enviar o arquivo')


def _friendly_b2_error(exc, action='acessar o arquivo'):
    """Converte erros técnicos do B2 em mensagens que o usuário consegue entender.

    Alguns erros do botocore não possuem ``response`` (ou podem expor
    ``response=None``). Nunca deixe o próprio tratamento de erro gerar um
    AttributeError e esconder a falha original.
    """
    response = getattr(exc, 'response', None) or {}
    error = response.get('Error') or {}
    metadata = response.get('ResponseMetadata') or {}
    code = str(error.get('Code', '')).strip()
    message = str(error.get('Message', '')).strip()
    status = metadata.get('HTTPStatusCode')

    if code in {'AccessDenied', 'UnauthorizedAccess', 'AllAccessDisabled', '403'} or status == 403:
        return StorageError(
            'O Portal Python não conseguiu acessar o Backblaze. O bucket pode estar privado sem uma chave com permissão para ler os arquivos, ou as credenciais do Render podem estar incorretas.',
            code='storage_unauthorized',
            technical=f'{code}: {message}' if code or message else str(exc),
        )

    if code == 'NoSuchBucket':
        return StorageError(
            'O bucket do Backblaze B2 não foi encontrado. Confira B2_BUCKET_NAME, B2_ENDPOINT e B2_REGION no Render.',
            code='storage_bucket_not_found',
            technical=f'{code}: {message}' if code or message else str(exc),
        )

    if code in {'NoSuchKey', 'NotFound', '404'} or (status == 404 and action in {'abrir o arquivo', 'excluir o arquivo'}):
        return StorageError(
            'O arquivo não foi encontrado no armazenamento do Portal Python. Ele pode ter sido removido ou o caminho do arquivo pode estar incorreto.',
            code='storage_not_found',
            technical=f'{code}: {message}' if code or message else str(exc),
        )

    if code in {'InvalidAccessKeyId', 'SignatureDoesNotMatch', 'InvalidToken', 'ExpiredToken'}:
        return StorageError(
            'As credenciais do Backblaze usadas pelo Portal Python são inválidas ou expiraram. Confira B2_KEY_ID/B2_APPLICATION_KEY_ID e B2_APPLICATION_KEY no Render.',
            code='storage_credentials',
            technical=f'{code}: {message}' if code or message else str(exc),
        )

    if code in {'InvalidBucketName', 'AuthorizationHeaderMalformed', 'InvalidRequest', 'PermanentRedirect'}:
        return StorageError(
            'A configuração do Backblaze B2 está incorreta. Confira o nome do bucket, B2_REGION e B2_ENDPOINT no Render. Para a região US East 005, use https://s3.us-east-005.backblazeb2.com e us-east-005.',
            code='storage_config',
            technical=f'{code}: {message}' if code or message else str(exc),
        )

    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return StorageError(
            f'Não foi possível {action} porque o Render não conseguiu conectar ao endpoint do Backblaze B2. Confira B2_ENDPOINT e B2_REGION.',
            code='storage_unavailable',
            technical=str(exc),
        )

    if isinstance(exc, BotoCoreError):
        return StorageError(
            f'Não foi possível {action} porque o serviço de armazenamento não respondeu corretamente. Confira o endpoint do Backblaze B2.',
            code='storage_unavailable',
            technical=str(exc),
        )

    safe_detail = code or message or exc.__class__.__name__
    return StorageError(
        f'Não foi possível {action} no momento. O Backblaze retornou: {safe_detail}. Confira B2_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME, B2_ENDPOINT e B2_REGION no Render.',
        code='storage_error',
        technical=f'{code}: {message}' if code or message else str(exc),
    )


def upload(file_storage, original_filename, content_type=None):
    """Upload permanente no Backblaze B2 usando a API nativa.

    A API nativa evita a camada S3/assinatura e usa diretamente a Application Key
    do B2, o que torna o upload independente de B2_ENDPOINT/B2_REGION.
    """
    if b2_enabled():
        safe_name = Path(original_filename).name.replace(' ', '_')
        import uuid
        key = f'materials/{uuid.uuid4().hex}-{safe_name}'
        return _native_upload(file_storage, key, content_type)

    folder = Path(current_app.config['UPLOAD_FOLDER'])
    folder.mkdir(parents=True, exist_ok=True)
    import uuid
    extension = Path(original_filename).suffix.lower()
    filename = f'{uuid.uuid4().hex}{extension}'
    file_storage.save(folder / filename)
    return filename


def delete(key):
    if not key:
        return
    if b2_enabled():
        try:
            _client().delete_object(Bucket=_bucket(), Key=key)
        except (BotoCoreError, ClientError) as exc:
            error = _friendly_b2_error(exc, 'excluir o arquivo')
            current_app.logger.warning('Falha ao excluir arquivo do Backblaze B2: %s | %s', key, error.technical)
        return
    try:
        (Path(current_app.config['UPLOAD_FOLDER']) / key).unlink()
    except FileNotFoundError:
        pass


def get_file(key):
    """Obtém um arquivo do B2 e devolve o objeto de resposta do S3.

    A leitura é feita pelo backend para que erros de permissão/configuração sejam
    tratados pelo Portal Python, em vez de o navegador exibir o XML do Backblaze.
    """
    if not b2_enabled():
        return None
    client = _client()
    try:
        return client.get_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        response = getattr(exc, 'response', None) or {}
        error = response.get('Error') or {}
        code = str(error.get('Code', '')).strip()
        status = (response.get('ResponseMetadata') or {}).get('HTTPStatusCode')
        # Arquivos criados pelo antigo Portal JM podem continuar referenciados
        # no banco. Fazemos uma leitura de compatibilidade, mas novos uploads
        # continuam indo exclusivamente para B2_BUCKET_NAME.
        legacy_bucket = _env('B2_LEGACY_BUCKET_NAME') or 'PortalJm'
        if key and legacy_bucket and legacy_bucket != _bucket() and (code in {'NoSuchKey', 'NotFound', '404'} or status == 404):
            try:
                return client.get_object(Bucket=legacy_bucket, Key=key)
            except (BotoCoreError, ClientError):
                pass
        raise _friendly_b2_error(exc, 'abrir o arquivo') from exc
    except BotoCoreError as exc:
        raise _friendly_b2_error(exc, 'abrir o arquivo') from exc


def presigned_url(key, expires=900):
    """Mantido para compatibilidade com integrações existentes.

    Para páginas do Portal Python, prefira get_file(), pois ele permite tratar no
    servidor os erros de acesso ao bucket privado.
    """
    if b2_enabled():
        try:
            return _client().generate_presigned_url(
                'get_object',
                Params={'Bucket': _bucket(), 'Key': key},
                ExpiresIn=expires,
            )
        except (BotoCoreError, ClientError) as exc:
            raise _friendly_b2_error(exc, 'gerar o acesso ao arquivo') from exc
    return None


def is_b2_key(key):
    return bool(key and key.startswith('materials/'))
