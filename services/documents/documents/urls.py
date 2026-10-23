from django.urls import path

from . import views

urlpatterns = [
    path('documents/', views.DocumentListCreateView.as_view(), name='document-list-create'),
    path('documents/<uuid:pk>/', views.DocumentDetailView.as_view(), name='document-detail'),
    path(
        'session-links/', views.SessionLinkListCreateView.as_view(), name='session-link-list-create'
    ),
    path(
        'session-links/<uuid:pk>/deactivate/',
        views.SessionLinkDeactivateView.as_view(),
        name='session-link-deactivate',
    ),
]
