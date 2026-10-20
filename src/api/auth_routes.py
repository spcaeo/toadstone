"""Signing in, signing up, and the three modes that bypass all of it.

Local mode, skip-auth and guest access each short-circuit this in a
different way, which is why they are gathered here rather than checked
in every route: the rules about who may do what are easier to audit when
they sit in one file.

Lifted out of main.py, which had grown past this project's 1,000-line
limit. Behaviour is unchanged; everything the routes used from main.py
is passed in rather than imported, so this module has no way to reach
back into the application it is mounted on.
"""

import os

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)

from src.auth_db import (authenticate_user, create_user, create_verification_token,
                         get_guest_crawls_last_24h, get_user_by_email, verify_token)


def create_blueprint(flags, helpers):
    """Build the blueprint.

    `flags` carries LOCAL_MODE, SKIP_AUTH, DISABLE_REGISTER and
    DISABLE_GUEST. `helpers` carries the functions main.py owns:
    login_required, get_client_ip, auto_login_local_mode, skip_auth_login,
    send_verification_email and send_welcome_email.
    """
    api = Blueprint('auth_routes', __name__)

    LOCAL_MODE = flags['LOCAL_MODE']
    SKIP_AUTH = flags['SKIP_AUTH']
    DISABLE_REGISTER = flags['DISABLE_REGISTER']
    DISABLE_GUEST = flags['DISABLE_GUEST']
    login_required = helpers['login_required']
    get_client_ip = helpers['get_client_ip']
    auto_login_local_mode = helpers['auto_login_local_mode']
    skip_auth_login = helpers['skip_auth_login']
    send_verification_email = helpers['send_verification_email']
    send_welcome_email = helpers['send_welcome_email']

    @api.route('/login')
    def login_page():
        # In local mode, auto-login and redirect to index
        if LOCAL_MODE:
            auto_login_local_mode()
            return redirect(url_for('index'))
        # Redirect to app if already logged in
        if 'user_id' in session:
            return redirect(url_for('index'))
        return render_template('login.html', registration_disabled=DISABLE_REGISTER, guest_disabled=DISABLE_GUEST, skip_auth=SKIP_AUTH)

    @api.route('/register')
    def register_page():
        # Redirect to app if already logged in
        if 'user_id' in session:
            return redirect(url_for('index'))
        return render_template('register.html', registration_disabled=DISABLE_REGISTER)

    @api.route('/verify')
    def verify_email():
        """Email verification endpoint"""
        token = request.args.get('token')

        if not token:
            return render_template('verification_result.html',
                                 success=False,
                                 message='Invalid verification link',
                                 app_source='main')

        # Verify the token
        success, message, app_source, user_email = verify_token(token)

        # Send welcome email if successful
        if success and user_email:
            try:
                user = get_user_by_email(user_email)
                if user:
                    send_welcome_email(user_email, user['username'], app_source or 'main')
            except Exception as e:
                print(f"Error sending welcome email: {e}")

        # Determine redirect URL based on app_source
        redirect_url = None
        if success:
            if app_source == 'workshop':
                redirect_url = os.getenv('WORKSHOP_APP_URL', 'https://workshop.toadstone.com')
            else:
                redirect_url = url_for('login_page')

        return render_template('verification_result.html',
                             success=success,
                             message=message,
                             app_source=app_source or 'main',
                             redirect_url=redirect_url)

    @api.route('/api/register', methods=['POST'])
    def register():
        # Check if registration is disabled
        if DISABLE_REGISTER:
            return jsonify({'success': False, 'message': 'Registration is currently disabled'})

        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')

        success, message, user_id = create_user(username, email, password)

        # In local mode, auto-verify and set to admin tier
        if success and LOCAL_MODE:
            try:
                from src.auth_db import verify_user, set_user_tier
                # Get the user that was just created
                import sqlite3
                conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
                user = cursor.fetchone()
                conn.close()

                if user:
                    verify_user(user['id'])
                    set_user_tier(user['id'], 'admin')
                    message = 'Account created and verified! You have admin access in local mode.'
            except Exception as e:
                print(f"Error during local mode auto-verification: {e}")
                # Don't fail the registration, just log the error
                # The account is still created successfully
        elif success:
            # Not in local mode - send verification email
            is_resend = (message == 'resend')
            try:
                # Create verification token
                token = create_verification_token(user_id, app_source='main')
                if token:
                    # Send verification email
                    email_success, email_message = send_verification_email(
                        email, username, token, app_source='main', is_resend=is_resend
                    )
                    if email_success:
                        if is_resend:
                            message = 'A verification email was already sent to this address. We\'ve updated your account details and sent a new verification link.'
                        else:
                            message = 'Registration successful! Please check your email to verify your account.'
                    else:
                        message = 'Account created, but we could not send the verification email. Please contact support.'
                        print(f"Email error: {email_message}")
                else:
                    message = 'Account created, but verification token generation failed. Please contact support.'
            except Exception as e:
                print(f"Error sending verification email: {e}")
                message = 'Account created, but we could not send the verification email. Please contact support.'

        return jsonify({'success': success, 'message': message})

    @api.route('/api/login', methods=['POST'])
    def login():
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''

        # Dangerously skip auth: accept any username with no password.
        # Username is only used to separate per-user sessions.
        if SKIP_AUTH:
            if not username:
                return jsonify({'success': False, 'message': 'Username required'})
            if len(username) > 50:
                return jsonify({'success': False, 'message': 'Username must be 50 characters or less'})
            success, message = skip_auth_login(username)
            return jsonify({'success': success, 'message': message})

        success, message, user_data = authenticate_user(username, password)

        if success:
            session['user_id'] = user_data['id']
            session['username'] = user_data['username']
            # In local mode, always give admin tier
            session['tier'] = 'admin' if LOCAL_MODE else user_data['tier']
            session.permanent = True  # Remember login

        return jsonify({'success': success, 'message': message})

    @api.route('/api/guest-login', methods=['POST'])
    def guest_login():
        """Login as a guest user (no account required, limited to 3 crawls/24h)"""
        if DISABLE_GUEST:
            return jsonify({'success': False, 'message': 'Guest login is disabled'})

        # Create a guest session with no user_id but with tier='guest'
        # In local mode, guests also get admin tier
        session['user_id'] = None
        session['username'] = 'Guest'
        session['tier'] = 'admin' if LOCAL_MODE else 'guest'
        session.permanent = False  # Don't persist guest sessions

        return jsonify({'success': True, 'message': 'Logged in as guest'})

    @api.route('/api/logout', methods=['POST'])
    @login_required
    def logout():
        session.clear()
        return jsonify({'success': True, 'message': 'Logged out successfully'})

    @api.route('/api/user/info')
    @login_required
    def user_info():
        """Get current user info including tier"""
        from src.auth_db import get_crawls_last_24h
        user_id = session.get('user_id')
        tier = session.get('tier', 'guest')
        username = session.get('username')

        # Get crawl count
        crawls_today = 0
        if tier == 'guest':
            # For guests, count from IP address
            client_ip = get_client_ip()
            crawls_today = get_guest_crawls_last_24h(client_ip)
        else:
            # For registered users, count from database
            crawls_today = get_crawls_last_24h(user_id)

        return jsonify({
            'success': True,
            'user': {
                'id': user_id,
                'username': username,
                'tier': tier,
                'crawls_today': crawls_today,
                'crawls_remaining': max(0, 3 - crawls_today) if tier == 'guest' else -1
            }
        })

    return api
