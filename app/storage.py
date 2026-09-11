import os
from pathlib import Path

import boto3
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
        raise StorageError(
            'O nome do bucket do Backblaze não foi configurado. Verifique B2_BUCKET_NAME.',
            code='storage_config',
        )
    return bucket


def _friendly_b2_error(exc, action='acessar o arquivo'):
    """Converte erros técnicos do B2 em mensagens que o usuário consegue entender."""
    error = getattr(exc, 'response', {}).get('Error', {}) if hasattr(exc, 'response') else {}
    code = str(error.get('Code', '')).strip()
    message = str(error.get('Message', '')).strip()
    status = getattr(exc, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode') if hasattr(exc, 'response') else None

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
    """Upload permanentemente no B2 quando configurado; caso contrário, salva localmente."""
    if b2_enabled():
        safe_name = Path(original_filename).name.replace(' ', '_')
        import uuid
        key = f'materials/{uuid.uuid4().hex}-{safe_name}'
        extra = {'ContentType': content_type or 'application/octet-stream'}
        try:
            client = _client()
            bucket = _bucket()
            stream = getattr(file_storage, 'stream', file_storage)
            # PutObject é suficiente para os arquivos do portal (limite de 25 MB)
            # e evita que o boto3 escolha multipart upload automaticamente.
            try:
                stream.seek(0)
            except (AttributeError, OSError):
                pass
            client.put_object(Bucket=bucket, Key=key, Body=stream, **extra)
        except (BotoCoreError, ClientError) as exc:
            raise _friendly_b2_error(exc, 'enviar o arquivo') from exc
        return key

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
        error = getattr(exc, 'response', {}).get('Error', {})
        code = str(error.get('Code', '')).strip()
        status = getattr(exc, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode')
        # Arquivos criados pelo antigo Portal JM podem continuar referenciados
        # no banco. Fazemos uma leitura de compatibilidade, mas novos uploads
        # continuam indo exclusivamente para B2_BUCKET_NAME (SitPython).
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
