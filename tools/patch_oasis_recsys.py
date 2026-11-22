"""
Let OASIS import with no torch / sentence-transformers / scikit-learn installed.

Background: oasis/social_platform/recsys.py imports torch, sentence_transformers and
sklearn at module level, so even random/popularity recommendations (reddit, random) need torch (about 1.2 GB).
Only the twitter personalized recommendation in MiroFish actually uses them.

This script turns those top-level imports into lazy imports and gives a clear message when a dependency is missing.
"""

import io
import os
import sys

TARGET_REL = os.path.join('oasis', 'social_platform', 'recsys.py')

# camel-oasis 0.2.5 hard-declares these dependencies in METADATA, but the MiroFish flow never uses them:
#   sentence-transformers / torch / scipy / scikit-learn -> twitter personalized recommendation only
#   cairocffi -> only used by the visualization scripts
#   neo4j -> AgentGraph uses the igraph backend by default
# With the declarations removed, pip/uv no longer pull in about 1.4 GB of dependencies.
STRIP_DEPS = ('torch', 'transformers', 'sentence-transformers', 'sentence_transformers',
              'scikit-learn', 'scipy', 'cairocffi', 'neo4j',
              # Development-time dependency, not needed at runtime
              'pre-commit', 'pytest', 'pytest-asyncio')


def strip_metadata(site_packages):
    """Remove the unnecessary Requires-Dist lines from camel_oasis METADATA; returns how many were removed."""
    import glob
    removed = 0
    for meta in glob.glob(os.path.join(site_packages, 'camel_oasis-*.dist-info', 'METADATA')):
        with io.open(meta, encoding='utf-8') as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            if line.startswith('Requires-Dist:'):
                # Either "sentence-transformers (==3.0.0)" or "torch>=2.0 ; extra == 'x'"
                name = line.split(':', 1)[1].strip()
                name = name.split(';')[0].strip()
                name = name.split('(')[0].strip()
                name = name.split('[')[0].strip()
                for sep in ('==', '>=', '<=', '~=', '!=', '>', '<', ' @'):
                    name = name.split(sep)[0].strip()
                name = name.lower()
                if name in STRIP_DEPS:
                    removed += 1
                    continue
            kept.append(line)
        with io.open(meta, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(kept)
    return removed
TARGET_REL_POSTS = os.path.join('oasis', 'social_platform', 'process_recsys_posts.py')

# The top-level dependency of process_recsys_posts.py (used to build embeddings; only the twitter personalized recommendation needs it)
POSTS_HEADER = '''import torch
from camel.embeddings import OpenAIEmbedding
from camel.types import EmbeddingModelType
from transformers import AutoModel, AutoTokenizer
'''

POSTS_REPLACEMENT = '''from camel.embeddings import OpenAIEmbedding
from camel.types import EmbeddingModelType


# ---------------------------------------------------------------------------
# Slim-mode patch: torch / transformers become lazy imports
# ---------------------------------------------------------------------------
class _MissingHeavyDeps(RuntimeError):
    pass


def _require_torch():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'This recommendation algorithm needs torch. Please install: pip install torch'
        ) from exc


def _require_transformers():
    try:
        from transformers import AutoModel, AutoTokenizer
        return AutoModel, AutoTokenizer
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'This recommendation algorithm needs transformers. Please install: pip install transformers'
        ) from exc


AutoModel = object   # only used for type annotations
AutoTokenizer = object
'''

HEADER = '''import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
'''

REPLACEMENT = '''# ---------------------------------------------------------------------------
# Slim-mode patch: torch / sentence-transformers / scikit-learn become lazy imports
# (applied automatically by the MiroFish installer; only the twitter personalized recommendation really needs them)
# ---------------------------------------------------------------------------
import numpy as np


class _MissingHeavyDeps(RuntimeError):
    pass


def _require_torch():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'The OASIS personalized recommendation needs torch. Please install: pip install torch '
            '(or switch to the reddit / random platform)'
        ) from exc


def _require_sentence_transformers():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'The OASIS personalized recommendation needs sentence-transformers.'
            'Please install: pip install sentence-transformers'
        ) from exc


def _require_sklearn():
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        return TfidfVectorizer, cosine_similarity
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'The OASIS personalized recommendation needs scikit-learn. Please install: pip install scikit-learn'
        ) from exc


class _LazyModule(object):
    """Import torch lazily through attribute access."""

    def __getattr__(self, name):
        return getattr(_require_torch(), name)


torch = _LazyModule()
'''

DEVICE_LINE = 'device = torch.device("cuda" if torch.cuda.is_available() else "cpu")'
DEVICE_REPLACEMENT = ('device = _require_torch().device('
                      '"cuda" if _require_torch().cuda.is_available() else "cpu")')


def patch_file(path):
    """Return patched / already / missing-header."""
    with io.open(path, encoding='utf-8') as f:
        source = f.read()

    if '_MissingHeavyDeps' in source:
        return 'already'

    heavy = ('import torch\n'
             'from sentence_transformers import SentenceTransformer\n'
             'from sklearn.feature_extraction.text import TfidfVectorizer\n'
             'from sklearn.metrics.pairwise import cosine_similarity\n')

    if HEADER in source:
        source = source.replace(HEADER, REPLACEMENT, 1)
    elif heavy in source:
        # The order is numpy -> torch -> sentence_transformers -> sklearn
        source = source.replace('import numpy as np\n' + heavy,
                                REPLACEMENT, 1)
    else:
        # Remove them line by line, keeping numpy
        lines = source.splitlines(keepends=True)
        drop_prefixes = ('import torch', 'from sentence_transformers',
                         'from sklearn.feature_extraction.text',
                         'from sklearn.metrics.pairwise')
        kept = [ln for ln in lines if not ln.startswith(drop_prefixes)]
        if len(kept) == len(lines):
            return 'missing-header'
        source = ''.join(kept)
        # Insert the patch code after numpy
        marker = 'import numpy as np\n'
        if marker not in source:
            return 'missing-header'
        source = source.replace(marker, marker + REPLACEMENT.split('import numpy as np\n', 1)[1], 1)
    # Replace only the module-level occurrence (the device inside functions is left untouched)
    source = source.replace(
        DEVICE_LINE,
        '# Device selection is deferred the same way (only the personalized recommendation needs it)\n'
        'device = None',
        1,
    )
    source = source.replace(
        "device = 'cuda' if torch.cuda.is_available() else 'cpu'",
        'device = None',
        1,
    )

    # Defer the module-level TfidfVectorizer instantiation and rename the local reference, so sklearn is not needed at import time
    source = source.replace(
        'tfidf_vectorizer = TfidfVectorizer()',
        '# Defer TfidfVectorizer until it is actually used (the reddit platform)\n'
        'tfidf_vectorizer = None',
    )
    source = source.replace(
        'cosine_similarities = cosine_similarity(user_vector, posts_vector)',
        'cosine_similarities = _require_sklearn()[1](user_vector, posts_vector)',
    )

    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(source)
    return 'patched'


def patch_posts_file(path):
    """Handle process_recsys_posts.py (the torch decorator + the top-level transformers import)."""
    with io.open(path, encoding='utf-8') as f:
        source = f.read()

    if '_MissingHeavyDeps' in source:
        return 'already'

    if POSTS_HEADER not in source:
        return 'missing-header'

    source = source.replace(POSTS_HEADER, POSTS_REPLACEMENT, 1)

    # @torch.no_grad() is a decorator, so it must exist at import time -> wrap it into a runtime lazy wrapper
    # The decorator is evaluated at import time; make it lazy at import time and validate for real at run time
    source = source.replace(
        '@torch.no_grad()\ndef process_batch(',
        '@_lazy_no_grad\ndef process_batch(',
    )
    source = source.replace(
        'AutoModel = object   # only used for type annotations',
        '''def _lazy_no_grad(func):
    """The lazy version of torch.no_grad(): the real dependency check is deferred until the function is called."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _require_torch().no_grad():
            return func(*args, **kwargs)

    return wrapper


AutoModel = object   # only used for type annotations''')
    source = source.replace(
        '    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")',
        '    torch = _require_torch()\n'
        '    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")',
    )
    source = source.replace(
        'def generate_post_vector(model: AutoModel, tokenizer: AutoTokenizer, texts,\n                         batch_size):',
        'def generate_post_vector(model: AutoModel, tokenizer: AutoTokenizer, texts,\n                         batch_size):\n    torch = _require_torch()',
    )

    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(source)
    return 'patched'


def strip_all_metadata(site_packages):
    """Slim mode: also strip the unnecessary dependency declarations from camel_oasis and camel_ai."""
    total = strip_metadata(site_packages)
    print(f'[patch_oasis_recsys] removed {total} unnecessary dependency declarations (sentence-transformers / torch and so on)')
    return total


def patch_site_packages(site_packages):
    target = os.path.join(site_packages, TARGET_REL)
    posts = os.path.join(site_packages, TARGET_REL_POSTS)
    if not os.path.exists(target):
        return 'not-found', target
    result = patch_file(target)
    if os.path.exists(posts):
        posts_result = patch_posts_file(posts)
        print(f'[patch_oasis_recsys] process_recsys_posts: {posts_result}')
    return result, target


def main(argv):
    # Either "directory --slim" or "--slim directory" works
    args = [a for a in argv[1:] if not a.startswith('--')]
    if args:
        target = args[0]
        if os.path.isdir(target):
            result, target = patch_site_packages(target)
        else:
            result = patch_file(target) if os.path.exists(target) else 'not-found'
    else:
        try:
            import oasis
        except ImportError:
            print('Usage: python patch_oasis_recsys.py <site-packages directory>')
            return 2
        target = os.path.dirname(oasis.__file__)
        result, target = patch_site_packages(os.path.dirname(target))

    print(f'[patch_oasis_recsys] {result}: {target}')

    slim = '--slim' in sys.argv or '--strip-metadata' in sys.argv
    if slim:
        # target may be recsys.py or the site-packages directory
        base = target if os.path.isdir(target) else os.path.dirname(target)
        if os.path.basename(base) == 'social_platform':
            base = os.path.dirname(os.path.dirname(base))
        strip_all_metadata(base)
        return 0

    return 0 if result in ('patched', 'already') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
