"""Keep the two API service configurations separate without exposing secrets."""
import urllib.parse

SERVICES=('deepseek','compatible')
FIELDS=('base_url','api_model','api_summary_model','api_context_window','secret')


def defaults():
    # Preserve the app's existing public defaults, but never inherit another
    # service's credential when creating a new profile.
    return dict(base_url='https://api.deepseek.com',api_model='deepseek-flash',
                api_summary_model='deepseek-flash',api_context_window=0,secret='')


def state(cfg):
    active=cfg.get('api_profile')
    if active not in SERVICES:
        # Legacy files had one shared connection. Attribute it by its actual
        # endpoint, even if the service selector previously said DeepSeek.
        active='compatible' if cfg.get('provider')=='compatible' else (
            'deepseek' if urllib.parse.urlsplit(cfg['base_url']).hostname=='api.deepseek.com' else 'compatible')
    profiles={service:{**defaults(),**cfg.get('api_connections',{}).get(service,{})} for service in SERVICES}
    profiles[active].update({key:cfg[key] for key in FIELDS if key in cfg})
    return active,profiles


def connection(cfg,service):
    if service not in SERVICES: raise ValueError('API 服务类型无效')
    _,profiles=state(cfg)
    return dict(cfg,**profiles[service],provider=service,api_profile=service)


def prepare(cfg,data):
    active,profiles=state(cfg)
    provider=data.get('provider',cfg['provider'])
    target=provider if provider in SERVICES else data.get('api_profile',active)
    if target not in SERVICES: raise ValueError('API 服务类型无效')
    return dict(cfg,**profiles[target],api_profile=target,api_connections=profiles)


def remember(cfg):
    profiles=dict(cfg['api_connections'])
    profiles[cfg['api_profile']]={key:cfg[key] for key in FIELDS}
    cfg['api_connections']=profiles
