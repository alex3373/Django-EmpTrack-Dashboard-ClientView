from datetime import datetime
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import IntegrityError, models
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from .models import Cliente, Dispositivo, Reparacion, Empleado, DetalleEtapa, Etapa, Notificacion
from .forms import EmpleadoRegistroForm, EmpleadoLoginForm, ReparacionForm, DetalleEtapaForm
import stripe
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse


TEMPLATE_REPARACIONES = 'miApp/reparaciones.html'



def reparacion_detalle(request, id):
    try:
        reparacion = Reparacion.objects.get(id=id)
    except Reparacion.DoesNotExist:
        return render(request, 'miApp/error.html', {'message': 'Reparación no encontrada'})

    if request.method == 'POST':
        form = DetalleEtapaForm(request.POST)
        if form.is_valid():
            detalle = form.save(commit=False)
            detalle.reparacion = reparacion
            detalle.empleado = request.user
            detalle.save()
            # Actualizar estado de la reparación
            reparacion.estado = detalle.etapa.nombre_etapa
            reparacion.save()
            return redirect('reparacion_detalle', id=reparacion.id)
    else:
        form = DetalleEtapaForm()
        etapas_disponibles = Etapa.objects.exclude(nombre_etapa__iexact='Entrega').exclude(nombre_etapa__iexact='Reparación')
        form.fields['etapa'].queryset = etapas_disponibles  # Asegúrate de pasar las etapas filtradas al formulario

    return render(request, 'miApp/reparacion_detalle.html', {
        'reparacion': reparacion,
        'form': form
    })


stripe.api_key = settings.STRIPE_SECRET_KEY

@csrf_exempt
def create_checkout_session(request):
    try:
        id_reparacion = request.GET.get('id_reparacion')
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Aprobación de reparación',
                    },
                    'unit_amount': 5000,  # $50.00 USD
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'http://localhost:8000/success/?id_reparacion={id_reparacion}',
            cancel_url='http://localhost:8000/cancel/',
        )
        return JsonResponse({'id': session.id})
    except Exception as e:
        return JsonResponse({'error': str(e)})


def success(request):
    id_reparacion = request.GET.get('id_reparacion')
    if id_reparacion:
        reparacion = get_object_or_404(Reparacion, id=id_reparacion)
        reparacion.estado = 'Reparación'
        reparacion.save()
    messages.success(request, '✅ ¡Pago exitoso y reparación aprobada!')
    return redirect(f"{reverse('seguimiento')}?id_reparacion={id_reparacion}")


def lista_reparaciones(request):
    # request.user ya es un Empleado
    reparaciones = Reparacion.objects.select_related(
        'dispositivo__cliente', 'empleado'
    ).filter(empleado=request.user).exclude(estado='Entrega')

    context = {
        'reparaciones': reparaciones
    }
    return render(request, 'miApp/reparaciones.html', context)



def payment_success(request):
    id_reparacion = request.GET.get('id_reparacion')

    if id_reparacion:
        reparacion = get_object_or_404(Reparacion, id=id_reparacion)
        reparacion.estado = 'Reparación'
        reparacion.save()
        messages.success(request, '✅ Pago exitoso. Reparación aprobada.')
        return redirect(f"{reverse('seguimiento')}?id_reparacion={id_reparacion}")

    return HttpResponse("Pago realizado, pero no se encontró la reparación.")




def es_admin(user):
    return user.is_superuser or user.rol == 'admin' 


def crear_reparacion(request):
    if request.method == 'POST':
        # Obtener los datos del formulario
        fecha_ingreso = request.POST.get('fecha_ingreso')
        marca = request.POST.get('marca')
        modelo = request.POST.get('modelo')
        numero_serie = request.POST.get('numero_serie')
        descripcion = request.POST.get('descripcion')
        nombre_cliente = request.POST.get('nombre_cliente')
        apellido_cliente = request.POST.get('apellido_cliente')
        correo_cliente = request.POST.get('correo_cliente')
        telefono_cliente = request.POST.get('telefono_cliente')
        empleado_id = request.POST.get('id_empleado')
        
        if not all([fecha_ingreso, marca, modelo, numero_serie, descripcion, nombre_cliente, apellido_cliente, correo_cliente, telefono_cliente, empleado_id]):
            return HttpResponse("Todos los campos son obligatorios.", status=400)
        
        try:
            fecha_ingreso = datetime.strptime(fecha_ingreso, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse("La fecha de ingreso no es válida.", status=400)
        
        cliente, _ = Cliente.objects.get_or_create(
            email=correo_cliente,
            defaults={'nombre': nombre_cliente, 'apellido': apellido_cliente, 'telefono': telefono_cliente}
        )
        
        dispositivo = Dispositivo.objects.create(
            marca=marca,
            modelo=modelo,
            numero_serie=numero_serie,
            descripcion=descripcion,
            cliente=cliente
        )
        
        try:
            empleado = Empleado.objects.get(id=empleado_id)
        except Empleado.DoesNotExist:
            return HttpResponse("Empleado no encontrado.", status=404)
        
        Reparacion.objects.create(
            fecha_ingreso=fecha_ingreso,
            estado="Pendiente",  
            dispositivo=dispositivo,
            empleado=empleado
        )
        
        # Redirigir a la lista de reparaciones después de crear
        return redirect('reparaciones_list') 
        
    else:
        empleados = Empleado.objects.all()  
        return render(request, 'crear_reparacion.html', {'empleados': empleados})


def reparaciones(request):
    try:
        empleado = Empleado.objects.get(user=request.user)
        reparaciones = Reparacion.objects.filter(empleado=empleado)
        print(f"Reparaciones: {reparaciones}")
        for r in reparaciones:
            print(f"Dispositivo: {r.dispositivo.marca}, Cliente: {r.dispositivo.cliente.nombre}")
            print(f"Empleado: {r.empleado.nombre}")
    except Empleado.DoesNotExist:
        reparaciones = []
        print("No se encontró un empleado para el usuario logueado")

    return render(request, 'reparaciones.html', {'reparaciones': reparaciones})


def pagar(request):
    return render(request, 'miApp/pagar.html', {
        'STRIPE_PUBLIC_KEY': settings.STRIPE_PUBLIC_KEY
    })

def index(request):
    return render(request, 'miApp/index.html')


def condiciones_servicio(request):
    return render(request, 'miApp/Condiciones.html')

def sobre_nosotros(request):
    return render(request, 'miApp/sobre_nosotros.html')

def contactanos(request):
    return render(request, 'miApp/contactanos.html')

def notificacion(request, id):
    reparacion = get_object_or_404(Reparacion, id=id)

    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        email = request.POST.get('email')
        mensaje = request.POST.get('mensaje')

        contenido = f"""
        <h2>Nuevo mensaje de contacto</h2>
        <p><strong>Nombre:</strong> {nombre}</p>
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Mensaje:</strong><br>{mensaje}</p>
        <p><strong>Detalles de la reparación:</strong><br>
        ID de Reparación: {reparacion.id}<br>
        Cliente: {reparacion.dispositivo.cliente.nombre} {reparacion.dispositivo.cliente.apellido}<br>
        Dispositivo: {reparacion.dispositivo.marca} {reparacion.dispositivo.modelo}</p>
        """

        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key['api-key'] = settings.BREVO_API_KEY  
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": email, "name": nombre}],
            sender={"email": "emptrackmanager@gmail.com", "name": "EmpTrack Manager"},
            subject="Formulario de Contacto - Django",
            html_content=contenido
        )

        try:
            api_response = api_instance.send_transac_email(send_smtp_email)
            if hasattr(api_response, 'message_id'):
                return HttpResponse(f'Correo enviado correctamente. ID: {api_response.message_id}')
            return HttpResponse(f'Correo enviado correctamente. Respuesta: {api_response}')
        except ApiException as e:
            return HttpResponse(f'Error al enviar el correo: {e}')

    return render(request, 'miApp/notificacion.html', {
        'reparacion_id': reparacion.id
    })

def notificacioncli(request, id):
    reparacion = get_object_or_404(Reparacion, id=id)

    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        email = request.POST.get('email')
        mensaje = request.POST.get('mensaje')

        contenido = f"""
        <h2>Nuevo mensaje de contacto</h2>
        <p><strong>Nombre:</strong> {nombre}</p>
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Mensaje:</strong><br>{mensaje}</p>
        <p><strong>Detalles de la reparación:</strong><br>
        ID de Reparación: {reparacion.id}<br>
        Cliente: {reparacion.dispositivo.cliente.nombre} {reparacion.dispositivo.cliente.apellido}<br>
        Dispositivo: {reparacion.dispositivo.marca} {reparacion.dispositivo.modelo}</p>
        """

        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key['api-key'] = settings.BREVO_API_KEY  
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": email, "name": nombre}],
            sender={"email": "emptrackmanager@gmail.com", "name": "EmpTrack Manager"},
            subject="Formulario de Contacto - Django",
            html_content=contenido
        )

        try:
            api_response = api_instance.send_transac_email(send_smtp_email)

            reparacion.estado = 'Entrega'
            reparacion.save()

            return redirect('reparaciones')  

        except ApiException as e:
            return HttpResponse(f'Error al enviar el correo: {e}')

    return render(request, 'miApp/notificacioncli.html', {
        'reparacion_id': reparacion.id
    })


def registro_empleado(request):
    if request.method == 'POST':
        form = EmpleadoRegistroForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form = EmpleadoRegistroForm()
    return render(request, 'miApp/registro.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = EmpleadoLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            
            user = authenticate(request, username=username, password=password)
            print(f"Intento de login - Usuario: {username}, Autenticado: {user is not None}")  # DEBUG

            if user is not None:
                login(request, user)
                return redirect('perfilempleado')
            else:
                form.add_error(None, "Usuario o contraseña incorrectos.")
    else:
        form = EmpleadoLoginForm()

    return render(request, 'miApp/login.html', {'form': form})

@login_required
def dashboard(request):
    # Contadores
    total_reparaciones = Reparacion.objects.count()  # Total de reparaciones en la base de datos
    en_proceso = Reparacion.objects.filter(estado__icontains="proceso").count()  # Reparaciones en proceso
    pendientes = Reparacion.objects.filter(estado__icontains="pendiente").count()  # Reparaciones pendientes
    completadas = Reparacion.objects.filter(estado__icontains="Entrega").count()  # Reparaciones completadas
    total_notificaciones = Notificacion.objects.count()  # Total de notificaciones

    # Reparaciones recientes (últimas 5)
    reparaciones_recientes = Reparacion.objects.select_related('dispositivo', 'dispositivo__cliente').order_by('-fecha_ingreso')[:5]

    # Contexto para pasar a la plantilla
    contexto = {
        'total_reparaciones': total_reparaciones,
        'en_proceso': en_proceso,
        'pendientes': pendientes,
        'completadas': completadas,
        'total_notificaciones': total_notificaciones,
        'reparaciones_recientes': reparaciones_recientes,
    }

    return render(request, 'miApp/dashboard.html', contexto)

def historial(request):
    historial = Reparacion.objects.select_related(
        'dispositivo__cliente', 'empleado'
    ).filter(estado='Entrega')

    context = {
        'historial': historial
    }
    return render(request, 'miApp/historial.html', context)

def reabrir_reparacion(request, id):
    reparacion = get_object_or_404(Reparacion, id=id)
    reparacion.estado = "Reparación"
    reparacion.save()
    return redirect('reparaciones')


def seguimiento(request):
    id_reparacion = request.GET.get('id_reparacion')

    if id_reparacion:
        reparacion = get_object_or_404(Reparacion, id=id_reparacion)

        accion = request.GET.get('accion')
        if accion == 'rechazar':
            reparacion.estado = 'Diágnostico'
            reparacion.save()
            messages.success(request, '⚠️ Reparación rechazada. El equipo continuará trabajando.')
            return redirect(f"{reverse('seguimiento')}?id_reparacion={id_reparacion}")

        etapas = reparacion.detalleetapa_set.all()

        return render(request, 'miApp/seguimiento.html', {
            'reparacion': reparacion,
            'etapas': etapas,
            'id_reparacion': id_reparacion,
            'STRIPE_PUBLIC_KEY': settings.STRIPE_PUBLIC_KEY  # <- Esto es lo nuevo
        })

    return render(request, 'miApp/seguimiento.html')




def cotizacion(request):
    return render(request, 'miApp/cotizacion.html')


def cotizacionin(request):
    return render(request, 'miApp/cotizacionin.html')

def asignar(request):
    if request.method == 'POST':
        try:
            # Obtener los datos del formulario
            fecha_ingreso = request.POST['fecha_ingreso']
            marca = request.POST['marca']
            modelo = request.POST['modelo']
            numero_serie = request.POST['numero_serie']
            descripcion = request.POST['descripcion']
            nombre_cliente = request.POST['nombre_cliente']
            apellido_cliente = request.POST['apellido_cliente']
            correo_cliente = request.POST['correo_cliente']
            telefono_cliente = request.POST['telefono_cliente']
            id_empleado = request.POST['id_empleado']

            # Validar y convertir la fecha de ingreso
            fecha_ingreso = datetime.strptime(fecha_ingreso, "%Y-%m-%d").date()

            # Crear cliente si no existe
            cliente, created = Cliente.objects.get_or_create(
                nombre=nombre_cliente,
                apellido=apellido_cliente,
                email=correo_cliente,
                telefono=telefono_cliente
            )

            if not created:
                messages.info(request, "El cliente ya existía en el sistema.")

            # Crear dispositivo asociado al cliente
            dispositivo = Dispositivo.objects.create(
                marca=marca,
                modelo=modelo,
                numero_serie=numero_serie,
                descripcion=descripcion,
                cliente=cliente
            )

            # Asignar el empleado
            empleado = Empleado.objects.get(id=id_empleado)

            # Crear la reparación
            reparacion = Reparacion.objects.create(
                dispositivo=dispositivo,  # No es necesario agregar 'cliente' aquí
                empleado=empleado,
                fecha_ingreso=fecha_ingreso,
                estado="Pendiente"  # Estado inicial de la reparación
            )

            # Guardar la reparación
            reparacion.save()

            messages.success(request, "Reparación asignada correctamente.")
            return redirect('reparaciones')  # Redirigir a la página de reparaciones

        except IntegrityError as e:
            messages.error(request, f"Error al guardar los datos: {str(e)}")
            return redirect('asignar')

        except Exception as e:
            messages.error(request, f"Error al asignar la reparación: {str(e)}")
            return redirect('asignar')

    # Si no es POST, cargar la página con los empleados
    empleados = Empleado.objects.all()
    return render(request, 'miApp/asignar.html', {'empleados': empleados})


@login_required
def reparaciones(request):
    return render(request, 'miApp/reparaciones.html')

@login_required
def perfilempleado(request):
    return render(request, 'miApp/perfilempleado.html', {'empleado': request.user})
