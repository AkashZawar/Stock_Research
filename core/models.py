"""No models.

Every tab reads live from its upstream on each request and caches in memory
only, so the app persists nothing between requests and needs no database. This
module is kept so the app remains a normal Django app package.
"""
