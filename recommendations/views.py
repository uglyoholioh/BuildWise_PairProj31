from django.http import HttpResponse


def placeholder(request, *args, **kwargs):
    return HttpResponse("Page under construction")
