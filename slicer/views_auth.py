from django.views.generic import FormView
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy, reverse
from django.contrib.auth import login
from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required, user_passes_test

class AdminUserCreationForm(UserCreationForm):
    ROLE_CHOICES = (
        ('guest', 'Guest User'),
        ('admin', 'Admin (staff)')
    )
    role = forms.ChoiceField(choices=ROLE_CHOICES, help_text="Select the role for the new user.")

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data.get('role')
        if role == 'admin':
            user.is_staff = True  # allow admin site access
        # superuser status is not granted here; reserved for system owner
        if commit:
            user.save()
        return user


class RegisterView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    """Admin-only user creation page (single template for all roles)."""
    template_name = 'slicer/register.html'
    form_class = AdminUserCreationForm
    success_url = reverse_lazy('dashboard')

    def test_func(self):
        return self.request.user.is_superuser  # only system owner can create users

    def handle_no_permission(self):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to create users.")

    def form_valid(self, form):
        form.save()  # do not auto-login the newly created user; admin stays logged in
        return super().form_valid(form)


class UnifiedLoginView(LoginView):
    template_name = 'slicer/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        # Check for next parameter first
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        # Default routing based on user role
        if user.is_authenticated and user.is_staff:
            return reverse('admin:index')
        return reverse('dashboard')


class UniversalLogoutView(LogoutView):
    """Logout view that redirects all users to login page regardless of role"""
    http_method_names = ['get', 'post']  # Allow both GET and POST
    template_name = 'slicer/login.html'  # Fallback template
    
    def get_success_url(self):
        # Always redirect to main login page, never admin
        return '/login/'
    
    def dispatch(self, request, *args, **kwargs):
        # Override to ensure we handle the logout properly
        response = super().dispatch(request, *args, **kwargs)
        # Force redirect to main login if somehow ending up elsewhere
        if hasattr(response, 'url') and '/admin/' in response.url:
            from django.shortcuts import redirect
            return redirect('/login/')
        return response


# Decorator functions for function-based views
@login_required
def protected_view_example(request):
    """Example of function-based view with login_required decorator"""
    pass


def is_superuser(user):
    """Test function for superuser check"""
    return user.is_superuser


@user_passes_test(is_superuser, login_url='/login/')
def superuser_only_view_example(request):
    """Example of superuser-only view with redirect to login"""
    pass
