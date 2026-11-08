# Reglas recomendadas del lenguaje Terraform (sin plugins de proveedor: no
# requiere credenciales ni descargas adicionales).
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}
