from django.shortcuts import render


def render_page(request, template_name):
    return render(request, template_name)


def index(request):
    return render_page(request, "index.html")


def events(request):
    return render_page(request, "events.html")


def dashboard(request):
    return render_page(request, "admin.html")


def upload(request):
    return render_page(request, "upload.html")


def orders(request):
    return render_page(request, "orders.html")


def promos(request):
    return render_page(request, "promos.html")


def purchased(request):
    return render_page(request, "purchased.html")


def legal(request):
    return render_page(request, "legal.html")
