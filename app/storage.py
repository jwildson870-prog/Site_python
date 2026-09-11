import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
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
    # O bucket SitPython está na região US East 005. Pode ser sobrescrito no Render.
    return _env('B2_ENDPOINT') or 'https://s3.us-east-005.backblazeb2.com'


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

    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=_key_id(),
        aws_secret_access_key=_env('B2_APPLICATION_KEY'),
        region_name=_env('B2_REGION') or 'us-east-1',
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

    if code in {'NoSuchBucket', 'NotFound', 'NoSuchKey', '404'} or status == 404:
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

    if code in {'InvalidBucketName', 'AuthorizationHeaderMalformed'}:
        return StorageError(
            'A configuração do bucket do Backblaze está incorreta. Confira o nome do bucket e a região/endpoint configurados no Render.',
            code='storage_config',
            technical=f'{code}: {message}' if code or message else str(exc),
        )

    if isinstance(exc, BotoCoreError):
        return StorageError(
            f'Não foi possível {action} porque o serviço de armazenamento não respondeu corretamente. Tente novamente em alguns instantes.',
            code='storage_unavailable',
            technical=str(exc),
        )

    return StorageError(
        f'Não foi possível {action} no momento. Verifique a configuração do Backblaze B2 no servidor.',
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
            _client().upload_fileobj(file_storage, _bucket(), key, ExtraArgs=extra)
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
    try:
        return _client().get_object(Bucket=_bucket(), Key=key)
    except (BotoCoreError, ClientError) as exc:
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
