from django.http import JsonResponse
from django.urls import path
from transcriptions import views

urlpatterns = [
    path("", lambda request: JsonResponse({
        "service": "Audio transcription API",
        "upload": "POST /transcriptions/ (multipart field: audio)",
        "status": "GET /transcriptions/<id>/",
        "result": "GET /transcriptions/<id>/result/",
        "retry": "POST /transcriptions/<id>/retry/",
    })),
    path("transcriptions/", views.upload),
    path("transcriptions/<uuid:job_id>/", views.detail),
    path("transcriptions/<uuid:job_id>/result/", views.result),
    path("transcriptions/<uuid:job_id>/retry/", views.retry),
]
