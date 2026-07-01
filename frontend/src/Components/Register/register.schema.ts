import * as z from "zod"

export const Schema = z.object({
    name: z.string().nonempty('name is required').min(3, 'name min length is 3'),
    email: z.email('email pattern is inavalid').nonempty('email is required'),
    password: z.string().nonempty('password is required')
        .regex(/(?=.*?[A-Z])/, 'Password must contain at least one uppercase letter (A–Z)')
        .regex(/(?=.*?[a-z])/, 'Password must contain at least one lowercase letter (a–z)')
        .regex(/(?=.*?[0-9])/, 'Password must contain at least one digit (0–9)')
        .regex(/(?=.*?[#?!@$%^&*-])/, 'Password must Contain at least one special character (# ? ! @ $ % ^ & * -)')
        .regex(/.{8,}/, 'Password must be at least 8 characters long'),
    confirmPassword: z.string().nonempty('Confirm-Password is required'),
    companyName: z.string().nonempty("Company Name is required"),
    industrialField: z.string().nonempty("Industrial Field is required")
}).refine(({ password, confirmPassword }) => {
    return password === confirmPassword
}, {
    path: ['confirmPassword'],
    error: 'Password confirmation is incorrect'
})



