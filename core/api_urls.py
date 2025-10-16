# core/api_urls.py
from django.urls import path
from . import api_views

urlpatterns = [
    path('bolsistas/', api_views.BolsistaListView.as_view(), name='api-bolsista-list'),
]